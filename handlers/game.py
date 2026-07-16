from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ContextTypes, CommandHandler
import config

async def game_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    game_url = f"{config.WEBHOOK_URL}/game"
    keyboard = [[InlineKeyboardButton("🐍 Play Snake", web_app=WebAppInfo(url=game_url))]]
    await update.message.reply_text(
        "🎮 **Snake Game**\n\n"
        "Control the snake and eat the food!\n"
        "🖥 **Desktop:** WASD / Arrow Keys\n"
        "📱 **Mobile:** Swipe to move\n\n"
        "Click the button below to open:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

game_handler = CommandHandler("game", game_command)
