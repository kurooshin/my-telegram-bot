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
from handlers.game import game_handler

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
        with open(game_path, 'r', encoding='utf-8') as f:
            self.write(f.read())
        self.set_header('Content-Type', 'text/html; charset=utf-8')

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

async def main():
    await database.init_db()

    application = Application.builder().token(config.TOKEN).build()
    application.add_handler(panel_conversation)
    application.add_handler(say_handler)
    application.add_handler(game_handler)
    application.add_handler(keyword_handler)

    await application.initialize()
    await application.start()

    port_number = int(os.environ.get("PORT", 8080))
    webhook_url = f"{config.WEBHOOK_URL}/{config.TOKEN}"
    await application.bot.set_webhook(url=webhook_url)

    tornado_app = tornado.web.Application([
        (f"/{config.TOKEN}", BotWebhookHandler, dict(bot_app=application)),
        ("/game", GameHandler),
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
