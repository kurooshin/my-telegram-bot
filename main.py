import os
import json
import asyncio
import logging
import tornado.web
import tornado.platform.asyncio
from telegram import Update
from telegram.ext import Application
import config
import database
from handlers.admin_panel import panel_conversation
from handlers.text_responses import keyword_handler
from handlers.say_command import say_handler
from handlers.game import game_handler, tello_handler, othello_callback_handler, leaderboard_handler, start_handler
from handlers.game import BOT_USERNAME as _game_bot_username
import othello_game

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)

class BotWebhookHandler(tornado.web.RequestHandler):
    def initialize(self, bot_app):
        self.bot_app = bot_app

    async def post(self):
        try:
            body = json.loads(self.request.body)
            update = Update.de_json(body, self.bot_app.bot)
            await self.bot_app.process_update(update)
        except Exception as e:
            logging.error(f"Webhook error: {e}")
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

class LeaderboardHandler(tornado.web.RequestHandler):
    async def get(self):
        try:
            data = await database.get_leaderboard()
            self.write(json.dumps(data, ensure_ascii=False))
            self.set_header('Content-Type', 'application/json')
        except Exception as e:
            logging.error(f"Leaderboard error: {e}")
            self.set_status(500)
            self.write(json.dumps([]))

    async def post(self):
        try:
            body = json.loads(self.request.body)
            user_id = body.get('user_id', '')
            user_name = body.get('user_name', 'Player')
            score = int(body.get('score', 0))
            if user_id and score > 0:
                await database.submit_score(user_id, user_name, score)
        except Exception as e:
            logging.error(f"Score submit error: {e}")
        self.write('ok')

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
            uid = body.get('user_id')
            r = int(body.get('row'))
            c = int(body.get('col'))
            state = await othello_game.make_move(gid, uid, r, c)
            self.write(json.dumps(state, ensure_ascii=False, default=str))
        except Exception as e:
            logging.error(f"Othello move error: {e}")
            self.set_status(400)
            self.write(json.dumps(None))

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
            uid = body.get('user_id')
            name = body.get('name', 'Player')[:30]
            text = body.get('text', '').strip()
            if not gid or not text:
                self.write(json.dumps({'ok': False}))
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
            logging.error(f"Othello chat error: {e}")
            self.set_status(400)
            self.write(json.dumps({'ok': False}))

async def main():
    await database.init_db()

    # Restore games and lobbies from DB
    await othello_game.restore_games()
    await othello_game.restore_lobbies()
    logging.info(f"Restored {len(othello_game.games)} Othello games and {len(othello_game.lobbies)} lobbies from DB")

    application = Application.builder().token(config.TOKEN).build()
    application.add_handler(panel_conversation)
    application.add_handler(say_handler)
    application.add_handler(game_handler)
    application.add_handler(tello_handler)
    application.add_handler(othello_callback_handler)
    application.add_handler(start_handler)
    application.add_handler(leaderboard_handler)
    application.add_handler(keyword_handler)

    await application.initialize()
    await application.start()

    # Get bot username for deep links
    me = await application.bot.get_me()
    import handlers.game
    handlers.game.BOT_USERNAME = me.username
    logging.info(f"Bot username: {me.username}")

    port_number = int(os.environ.get("PORT", 8080))
    webhook_path = "/webhook"
    webhook_url = f"{config.WEBHOOK_URL}{webhook_path}"
    await application.bot.set_webhook(url=webhook_url)

    tornado_app = tornado.web.Application([
        (webhook_path, BotWebhookHandler, dict(bot_app=application)),
        ("/game", GameHandler),
        ("/tello", TelloHandler),
        ("/api/othello/state", OthelloStateHandler),
        ("/api/othello/move", OthelloMoveHandler),
        ("/api/othello/chat", OthelloChatHandler),
        ("/api/leaderboard", LeaderboardHandler),
    ])
    tornado_app.listen(port_number, "0.0.0.0")

    logging.info("Bot and game server running on port %d", port_number)
    try:
        await asyncio.Event().wait()
    finally:
        await application.stop()

if __name__ == '__main__':
    asyncio.run(main())
