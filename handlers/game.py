import logging
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, filters
import config
import database
import othello_game

def game_button(text, url, use_webapp, chat_type):
    if use_webapp and chat_type not in ("group", "supergroup"):
        return {"text": text, "web_app": {"url": url}}
    return {"text": text, "url": url}

async def game_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        base = config.WEBHOOK_URL
        ct = update.effective_chat.type
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="🎮 **Game Hub**\n\n"
                 "🐍 **Snake** — classic single-player\n"
                 "➡ Click below to play\n\n"
                 "⚫ **Othello (Reversi)** — 2-player matchmaking\n"
                 "➡ Use /tello to find an opponent",
            api_kwargs={
                "reply_markup": {
                    "inline_keyboard": [[
                        game_button("🐍 Play Snake", f"{base}/game", True, ct)
                    ]]
                }
            }
        )
    except Exception as e:
        logging.error(f"Game command error: {e}")
        await update.message.reply_text(f"❌ Error: {e}")

async def tello_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user
        uid = str(user.id)
        name = user.first_name or "Player"
        chat_id = update.effective_chat.id

        # Check if already in a game
        for gid, g in list(othello_game.games.items()):
            if g['black']['id'] == uid or g['white']['id'] == uid:
                base = config.WEBHOOK_URL
                url = f"{base}/tello?game_id={gid}"
                ct = update.effective_chat.type
                btn = game_button("⚫ Continue Game", url, True, ct)
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="⚫ You already have an active Othello game!",
                    api_kwargs={"reply_markup": {"inline_keyboard": [[btn]]}}
                )
                return

        # Check if already in queue
        if any(q['user_id'] == uid for q in othello_game.waiting_queue):
            await update.message.reply_text("⏳ You're already in the queue! Waiting for an opponent...")
            return

        # Join queue
        othello_game.waiting_queue.append({'user_id': uid, 'name': name, 'chat_id': chat_id})
        await update.message.reply_text("⏳ Searching for an Othello opponent...\n\nYou'll be notified when a match is found.")

        # Try to match
        if len(othello_game.waiting_queue) >= 2:
            p1 = othello_game.waiting_queue.pop(0)
            p2 = othello_game.waiting_queue.pop(0)

            # Check if they're the same user (shouldn't happen but just in case)
            if p1['user_id'] == p2['user_id']:
                othello_game.waiting_queue.insert(0, p1)
                return

            gid = othello_game.create_game(p1['user_id'], p1['name'], p2['user_id'], p2['name'])
            base = config.WEBHOOK_URL
            url = f"{base}/tello?game_id={gid}"

            for p in [p1, p2]:
                ct = "private"
                btn = game_button("⚫ Play Othello", url, True, ct)
                try:
                    await context.bot.send_message(
                        chat_id=p['chat_id'],
                        text=f"⚫ **Match Found!**\n\n{p1['name']} (●) vs {p2['name']} (○)\n\nClick below to play:",
                        api_kwargs={"reply_markup": {"inline_keyboard": [[btn]]}}
                    )
                except Exception as e:
                    logging.error(f"Failed to notify player {p['user_id']}: {e}")

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

game_handler = CommandHandler("game", game_command)
tello_handler = CommandHandler("tello", tello_command)
leaderboard_handler = CommandHandler("leaderboard", leaderboard_command)