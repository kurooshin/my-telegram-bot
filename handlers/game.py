import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ContextTypes, CommandHandler, filters
import config
import database

async def game_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        game_url = f"{config.WEBHOOK_URL}/game"
        keyboard = [[InlineKeyboardButton("🐍 Play Snake", web_app=WebAppInfo(url=game_url))]]
        await update.message.reply_text(
            "🎮 Snake Game\n\n"
            "Desktop: WASD / Arrow Keys\n"
            "Mobile: Swipe to move\n\n"
            "Click below to play:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logging.error(f"Game command error: {e}")
        await update.message.reply_text(f"❌ Error: {e}")

async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        rows = await database.get_leaderboard()
        if not rows:
            await update.message.reply_text("🏆 No scores yet. Be the first!")
            return
        text = "🏆 Snake Leaderboard\n\n"
        for i, r in enumerate(rows, 1):
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i}.")
            name = (r['user_name'] or 'Player')[:20]
            text += f"{medal} {name} — {r['score']}\n"
        await update.message.reply_text(text)
    except Exception as e:
        logging.error(f"Leaderboard error: {e}")
        await update.message.reply_text("❌ Error loading leaderboard.")

game_handler = CommandHandler("game", game_command)
leaderboard_handler = CommandHandler("leaderboard", leaderboard_command)
