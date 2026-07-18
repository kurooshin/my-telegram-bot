import logging
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, filters
import config
import database

def game_button(text, url, use_webapp, chat_type):
    if use_webapp and chat_type not in ("group", "supergroup"):
        return {"text": text, "web_app": {"url": url}}
    return {"text": text, "url": url}

async def games_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        base = config.WEBHOOK_URL
        ct = update.effective_chat.type
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="🎮 **Game Hub**\n\nChoose a game to play:",
            api_kwargs={
                "reply_markup": {
                    "inline_keyboard": [[
                        game_button("🐍 Snake", f"{base}/game", True, ct),
                        game_button("⚫ Othello", f"{base}/tello", False, ct)
                    ]]
                }
            }
        )
    except Exception as e:
        logging.error(f"Games command error: {e}")
        await update.message.reply_text(f"❌ Error: {e}")

async def game_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        game_url = f"{config.WEBHOOK_URL}/game"
        chat_type = update.effective_chat.type
        btn = game_button("🐍 Play Snake", game_url, True, chat_type)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="🎮 Snake Game\n\nDesktop: WASD / Arrow Keys | Space=Pause\nMobile: Swipe to move\n\nClick below to play:",
            api_kwargs={
                "reply_markup": {
                    "inline_keyboard": [[btn]]
                }
            }
        )
    except Exception as e:
        logging.error(f"Game command error: {e}")
        await update.message.reply_text(f"❌ Error: {e}")

async def tello_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        game_url = f"{config.WEBHOOK_URL}/tello"
        btn = game_button("⚫ Play Othello", game_url, False, "group")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="⚫ Othello (Reversi)\n\n2-player local hot-seat\nClick a valid move to place your disc\n\nClick below to play:",
            api_kwargs={
                "reply_markup": {
                    "inline_keyboard": [[btn]]
                }
            }
        )
    except Exception as e:
        logging.error(f"Tello command error: {e}")
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

games_handler = CommandHandler("games", games_command)
game_handler = CommandHandler("game", game_command)
tello_handler = CommandHandler("tello", tello_command)
leaderboard_handler = CommandHandler("leaderboard", leaderboard_command)
