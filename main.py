import os
import json
import asyncio
import logging
import tornado.web
import tornado.httpserver
import tornado.ioloop
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
    def initialize(self, application):
        self.bot_app = application

    async def post(self):
        try:
            body = json.loads(self.request.body)
            update = Update.de_json(body, self.bot_app.bot)
            await self.bot_app.process_update(update)
            self.set_status(200)
            self.write('ok')
        except Exception as e:
            logging.error(f"Webhook error: {e}")
            self.set_status(200)
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
            self.set_status(200)
            self.write('ok')
        except Exception as e:
            logging.error(f"Score submit error: {e}")
            self.set_status(200)
            self.write('ok')

def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    loop.run_until_complete(database.init_db())

    application = Application.builder().token(config.TOKEN).build()
    application.add_handler(panel_conversation)
    application.add_handler(say_handler)
    application.add_handler(game_handler)
    application.add_handler(keyword_handler)

    port_number = int(os.environ.get("PORT", 8080))

    webhook_path = f"/{config.TOKEN}"
    webhook_url = f"{config.WEBHOOK_URL}/{config.TOKEN}"

    tornado_app = tornado.web.Application([
        (webhook_path, BotWebhookHandler, dict(application=application)),
        (r"/game", GameHandler),
        (r"/api/leaderboard", LeaderboardHandler),
    ])

    server = tornado.httpserver.HTTPServer(tornado_app)
    server.listen(port_number, "0.0.0.0")

    async def startup():
        await application.initialize()
        await application.start()
        await application.bot.set_webhook(url=webhook_url)
        logging.info("Bot started and webhook set")

    loop.run_until_complete(startup())
    print("Bot and game server running...")
    try:
        tornado.ioloop.IOLoop.current().start()
    except KeyboardInterrupt:
        pass
    finally:
        loop.run_until_complete(application.stop())
        loop.close()

if __name__ == '__main__':
    main()
