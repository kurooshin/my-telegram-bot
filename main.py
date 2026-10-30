import os
import json
import asyncio
import logging
import signal
import tornado.web
import tornado.platform.asyncio
from telegram import Update
from telegram.ext import Application
import config
import database
import ai_service
import ratelimit
import tg_auth
from handlers.admin_panel import panel_conversation, toggle_group_handler, debug_ai_handler
from handlers.text_responses import keyword_handler
from handlers.say_command import say_handler
from handlers.smart_reply import (
    smart_reply_learning,
    suggest_handler,
    suggestion_callback_handler,
    forget_me_handler,
    privacy_handler,
)
from handlers.game import game_handler, tello_handler, othello_callback_handler, leaderboard_handler, start_handler
import othello_game

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)
# جلوگیری از چاپ توکن بات توی لاگ‌های httpx
logging.getLogger("httpx").setLevel(logging.WARNING)


async def error_handler(update: Update, context):
    logger.error("Unhandled update: %s | error: %s", update, context.error)


class BotWebhookHandler(tornado.web.RequestHandler):
    def initialize(self, bot_app):
        self.bot_app = bot_app

    async def post(self):
        try:
            body = json.loads(self.request.body)
            update = Update.de_json(body, self.bot_app.bot)
            await self.bot_app.process_update(update)
        except Exception as e:
            logger.error(f"Webhook error: {e}")
        self.write('ok')


class GameHandler(tornado.web.RequestHandler):
    def get(self):
        game_path = os.path.join(os.path.dirname(__file__), 'web', 'game.html')
        self.set_header('Content-Type', 'text/html; charset=utf-8')
        self.set_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.set_header('Pragma', 'no-cache')
        self.set_header('Expires', '0')
        with open(game_path, 'r', encoding='utf-8') as f:
            self.write(f.read())


class TelloHandler(tornado.web.RequestHandler):
    def get(self):
        game_path = os.path.join(os.path.dirname(__file__), 'web', 'tello.html')
        self.set_header('Content-Type', 'text/html; charset=utf-8')
        self.set_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.set_header('Pragma', 'no-cache')
        self.set_header('Expires', '0')
        with open(game_path, 'r', encoding='utf-8') as f:
            self.write(f.read())


def verified_telegram_user(handler: tornado.web.RequestHandler) -> dict | None:
    """Return the Telegram user proven by the X-Telegram-Init-Data header, else None.

    The header carries Telegram.WebApp.initData, which Telegram signs with the
    bot token — so unlike a user_id in the JSON body it cannot be forged.
    """
    init_data = handler.request.headers.get('X-Telegram-Init-Data', '')
    return tg_auth.verify_init_data(init_data, config.TOKEN)


class LeaderboardHandler(tornado.web.RequestHandler):
    async def get(self):
        self.set_header('Content-Type', 'application/json')
        try:
            top = await database.get_leaderboard()
            me = None
            user = verified_telegram_user(self)
            if user:
                me = await database.get_user_rank(user['id'])
            self.write(json.dumps({"top": top, "me": me}, ensure_ascii=False))
        except Exception as e:
            logger.error(f"Leaderboard error: {e}")
            self.set_status(500)
            self.write(json.dumps({"top": [], "me": None}))

    async def post(self):
        self.set_header('Content-Type', 'application/json')
        user = verified_telegram_user(self)
        if not user:
            # Not opened from inside Telegram (or forged data): never store a score.
            self.set_status(403)
            self.write(json.dumps({"ok": False, "error": "unauthorized"}))
            return
        try:
            if not ratelimit.check_rate_limit(f"score:{user['id']}", 3, 10):
                self.set_status(429)
                self.write(json.dumps({"ok": False, "error": "rate_limit"}))
                return
            body = json.loads(self.request.body)
            score = int(body.get('score', 0))
            if not (0 < score <= database.SNAKE_MAX_SCORE):
                self.set_status(400)
                self.write(json.dumps({"ok": False, "error": "bad_score"}))
                return
            result = await database.submit_score(
                user['id'], user.get('username'), tg_auth.display_name(user), score
            )
            self.write(json.dumps({"ok": True, **result}))
        except Exception as e:
            logger.error(f"Score submit error: {e}")
            self.set_status(500)
            self.write(json.dumps({"ok": False}))


class HealthHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header('Content-Type', 'application/json')
        self.write(json.dumps({
            "status": "ok",
            "games": len(othello_game.games),
            "lobbies": len(othello_game.lobbies),
        }))


class OthelloStateHandler(tornado.web.RequestHandler):
    def get(self):
        gid = self.get_query_argument('game_id', None)
        self.set_header('Content-Type', 'application/json')
        if not gid:
            self.write(json.dumps(None))
            return
        state = othello_game.get_state(gid)
        self.write(json.dumps(state, ensure_ascii=False, default=str))


class OthelloMoveHandler(tornado.web.RequestHandler):
    async def post(self):
        self.set_header('Content-Type', 'application/json')
        try:
            body = json.loads(self.request.body)
            gid = body.get('game_id')
            uid = body.get('user_id', '')
            if not ratelimit.check_rate_limit(f"move:{uid}", 5, 2):
                self.set_status(429)
                self.write(json.dumps({"error": "rate_limit"}))
                return
            r = int(body.get('row'))
            c = int(body.get('col'))
            state = await othello_game.make_move(gid, uid, r, c)
            self.write(json.dumps(state, ensure_ascii=False, default=str))
        except Exception as e:
            logger.error(f"Othello move error: {e}")
            self.set_status(400)
            self.write(json.dumps(None))


class OthelloResignHandler(tornado.web.RequestHandler):
    async def post(self):
        self.set_header('Content-Type', 'application/json')
        # The caller must prove who they are, otherwise anyone holding the game
        # link could resign on behalf of a player.
        user = verified_telegram_user(self)
        if not user:
            self.set_status(403)
            self.write(json.dumps({"error": "unauthorized"}))
            return
        try:
            if not ratelimit.check_rate_limit(f"resign:{user['id']}", 3, 5):
                self.set_status(429)
                self.write(json.dumps({"error": "rate_limit"}))
                return
            body = json.loads(self.request.body)
            state = await othello_game.resign(body.get('game_id'), str(user['id']))
            if state is None:
                self.set_status(400)
                self.write(json.dumps({"error": "cannot_resign"}))
                return
            self.write(json.dumps(state, ensure_ascii=False, default=str))
        except Exception as e:
            logger.error(f"Othello resign error: {e}")
            self.set_status(400)
            self.write(json.dumps({"error": "bad_request"}))


class OthelloChatHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header('Content-Type', 'application/json')
        gid = self.get_query_argument('game_id', None)
        if not gid:
            self.write(json.dumps([]))
            return
        msgs = othello_game.get_chat_messages(gid)
        self.write(json.dumps(msgs, ensure_ascii=False, default=str))

    async def post(self):
        self.set_header('Content-Type', 'application/json')
        try:
            body = json.loads(self.request.body)
            gid = body.get('game_id')
            uid = body.get('user_id', '')
            name = body.get('name', 'Player')[:30]
            text = body.get('text', '').strip()
            if not gid or not text:
                self.write(json.dumps({'ok': False}))
                return
            if not ratelimit.check_rate_limit(f"chat:{uid}", 10, 5):
                self.write(json.dumps({'ok': False, 'error': 'rate_limit'}))
                return
            g = othello_game.games.get(gid)
            if not g or g['game_over']:
                self.write(json.dumps({'ok': False}))
                return
            if uid != g['black']['id'] and uid != g['white']['id'] and uid != 'black' and uid != 'white':
                self.write(json.dumps({'ok': False}))
                return
            othello_game.add_chat_message(gid, uid, name, text)
            self.write(json.dumps({'ok': True}))
        except Exception as e:
            logger.error(f"Othello chat error: {e}")
            self.set_status(400)
            self.write(json.dumps({'ok': False}))


async def shutdown(sig, stop_event):
    logger.info(f"Received {sig.name}, initiating graceful shutdown...")
    stop_event.set()


async def main():
    config.check_config()
    try:
        await database.init_db()
    except Exception as e:
        logger.error("Database init failed (non-fatal, trying to continue): %s", e, exc_info=True)

    ai_service.check_api_key()

    await othello_game.restore_games()
    await othello_game.restore_lobbies()
    logger.info(f"Restored {len(othello_game.games)} Othello games and {len(othello_game.lobbies)} lobbies from DB")

    application = Application.builder().token(config.TOKEN).build()
    application.add_error_handler(error_handler)
    application.add_handler(panel_conversation)
    application.add_handler(toggle_group_handler)
    application.add_handler(debug_ai_handler)
    application.add_handler(say_handler)
    application.add_handler(game_handler)
    application.add_handler(tello_handler)
    application.add_handler(othello_callback_handler)
    application.add_handler(start_handler)
    application.add_handler(leaderboard_handler)
    application.add_handler(suggest_handler)
    application.add_handler(suggestion_callback_handler)
    application.add_handler(forget_me_handler)
    application.add_handler(privacy_handler)

    # smart_reply_learning (message tracking for style-learning) and keyword_handler
    # (keyword replies / AI group chat) both match filters.TEXT & ~filters.COMMAND.
    # PTB only runs the FIRST matching handler within a given group, so they are
    # registered in separate groups here on purpose — both must run independently
    # on every group text message, since one only logs/counts and the other decides
    # whether to send a reply. Do not merge them back into the same group.
    application.add_handler(smart_reply_learning, group=1)
    application.add_handler(keyword_handler, group=0)

    await application.initialize()
    await application.start()

    me = await application.bot.get_me()
    import handlers.game
    handlers.game.BOT_USERNAME = me.username
    logger.info(f"Bot username: {me.username}")

    # Set bot commands
    commands = [
        ("start", "ورود و شروع ربات"),
        ("game", "منوی بازی‌ها (ماربازی و اتللو)"),
        ("tello", "شروع یا پیوستن به لابی اتللو"),
        ("leaderboard", "جدول رده‌بندی برترین‌ها"),
        ("menu", "منوی مدیریت"),
        ("panel", "پنل مدیریت ربات"),
        ("suggest", "پیشنهاد پاسخ هوشمند به سبک تو"),
        ("forget_me", "حذف کامل داده‌های ذخیره‌شده"),
        ("privacy", "توضیح حریم خصوصی"),
    ]
    await application.bot.set_my_commands(commands)
    logger.info("Bot commands registered")

    port_number = int(os.environ.get("PORT", 8080))
    webhook_path = "/webhook"
    webhook_url = f"{config.WEBHOOK_URL}{webhook_path}"
    # allowed_updates رو صریح میدیم تا دکمه‌های شیشه‌ای (callback_query) حتماً به بات برسن
    await application.bot.set_webhook(url=webhook_url, allowed_updates=Update.ALL_TYPES)

    tornado_app = tornado.web.Application([
        (webhook_path, BotWebhookHandler, dict(bot_app=application)),
        ("/game", GameHandler),
        ("/tello", TelloHandler),
        ("/api/othello/state", OthelloStateHandler),
        ("/api/othello/move", OthelloMoveHandler),
        ("/api/othello/resign", OthelloResignHandler),
        ("/api/othello/chat", OthelloChatHandler),
        ("/api/leaderboard", LeaderboardHandler),
        ("/health", HealthHandler),
    ])
    tornado_app.listen(port_number, "0.0.0.0")
    logger.info("Bot and game server running on port %d", port_number)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(shutdown(s, stop_event)))

    try:
        await stop_event.wait()
    finally:
        logger.info("Shutting down bot server...")
        tornado_app.stop()
        await database.close_pool()
        await application.stop()


if __name__ == '__main__':
    asyncio.run(main())
