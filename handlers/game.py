import logging
from telegram import Update, InlineKeyboardButton
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, filters
import config
import database
import othello_game

def game_button(text, url, use_webapp, chat_type):
    if use_webapp and chat_type not in ("group", "supergroup"):
        return {"text": text, "web_app": {"url": url}}
    return {"text": text, "url": url}

def othello_buttons():
    return [
        [{"text": "✅ Join Othello", "callback_data": "othello_join"},
         {"text": "❌ Leave Queue", "callback_data": "othello_leave"}]
    ]

async def game_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        base = config.WEBHOOK_URL
        ct = update.effective_chat.type
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="🎮 **Game Hub**\n\n"
                 "🐍 **Snake** — classic single-player\n"
                 "⚫ **Othello** — 2-player matchmaking",
            api_kwargs={
                "reply_markup": {
                    "inline_keyboard": [[
                        game_button("🐍 Snake", f"{base}/game", True, ct)
                    ]] + othello_buttons()
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
        await show_othello_menu(context, chat_id, uid, name)
    except Exception as e:
        logging.error(f"Tello command error: {e}")
        await update.message.reply_text(f"❌ Error: {e}")

async def show_othello_menu(context, chat_id, uid, name):
    # Check if in an active game
    for gid, g in list(othello_game.games.items()):
        if g['black']['id'] == uid or g['white']['id'] == uid:
            base = config.WEBHOOK_URL
            url = f"{base}/tello?game_id={gid}"
            btn = game_button("⚫ Continue Game", url, True, "private")
            await context.bot.send_message(
                chat_id=chat_id,
                text="⚫ You already have an active Othello game!",
                api_kwargs={"reply_markup": {"inline_keyboard": [[btn]]}}
            )
            return

    in_queue = any(q['user_id'] == uid for q in othello_game.waiting_queue)
    text = "⏳ You're in queue!" if in_queue else "⚫ **Othello (Reversi)** — 2-player matchmaking"
    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        api_kwargs={"reply_markup": {"inline_keyboard": othello_buttons()}}
    )

async def othello_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    uid = str(user.id)
    name = user.first_name or "Player"
    chat_id = update.effective_chat.id

    data = query.data

    if data == "othello_join":
        # Check if already in a game
        for gid, g in list(othello_game.games.items()):
            if g['black']['id'] == uid or g['white']['id'] == uid:
                await context.bot.send_message(chat_id=chat_id, text="⚫ You already have an active Othello game!")
                return

        if any(q['user_id'] == uid for q in othello_game.waiting_queue):
            await context.bot.send_message(chat_id=chat_id, text="⏳ You're already in the queue!")
            return

        othello_game.waiting_queue.append({'user_id': uid, 'name': name, 'chat_id': chat_id})
        await context.bot.send_message(
            chat_id=chat_id,
            text="⏳ Joined the Othello queue!\n\nWaiting for an opponent...",
            api_kwargs={"reply_markup": {"inline_keyboard": othello_buttons()}}
        )

        # Try to match
        if len(othello_game.waiting_queue) >= 2:
            p1 = othello_game.waiting_queue.pop(0)
            p2 = othello_game.waiting_queue.pop(0)

            if p1['user_id'] == p2['user_id']:
                othello_game.waiting_queue.insert(0, p1)
                return

            gid = othello_game.create_game(p1['user_id'], p1['name'], p2['user_id'], p2['name'])
            base = config.WEBHOOK_URL
            url = f"{base}/tello?game_id={gid}"

            notifs = [
                (p1, p2['name'], "●", "Black"),
                (p2, p1['name'], "○", "White"),
            ]
            for player, opp_name, opp_sym, my_color in notifs:
                try:
                    await context.bot.send_message(
                        chat_id=player['chat_id'],
                        text=f"⚫ **Match Found!**\n\n{opp_name} ({opp_sym}) vs **You** ({my_color})\n\nClick below to play:",
                        api_kwargs={
                            "reply_markup": {"inline_keyboard": [[
                                game_button("⚫ Play Othello", url, True, "private")
                            ]]}
                        }
                    )
                except Exception as e:
                    logging.error(f"Failed to notify {player['user_id']}: {e}")

    elif data == "othello_leave":
        was_in = any(q['user_id'] == uid for q in othello_game.waiting_queue)
        othello_game.waiting_queue[:] = [q for q in othello_game.waiting_queue if q['user_id'] != uid]
        text = "✅ Left the Othello queue." if was_in else "❌ You're not in the Othello queue."
        await context.bot.send_message(chat_id=chat_id, text=text, api_kwargs={"reply_markup": {"inline_keyboard": othello_buttons()}})

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
othello_callback_handler = CallbackQueryHandler(othello_callback, pattern="^othello_")
leaderboard_handler = CommandHandler("leaderboard", leaderboard_command)