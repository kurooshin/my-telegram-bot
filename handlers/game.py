import asyncio
import logging
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters
import config
import database
import othello_game

lobby_tasks = {}
BOT_USERNAME = None

async def _lobby_timeout(chat_id, bot):
    try:
        await asyncio.sleep(120)
        lobby = othello_game.lobbies.get(chat_id)
        if lobby and len(lobby['players']) < 2:
            del othello_game.lobbies[chat_id]
            await database.delete_othello_lobby(chat_id)
            await bot.send_message(
                chat_id=chat_id,
                text="⏰ **Time up!** Not enough players joined. Lobby closed.\n\nUse /game or /tello to start a new one."
            )
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logging.error(f"Lobby timeout error: {e}")

def schedule_lobby_timeout(chat_id, bot):
    cancel_lobby_timeout(chat_id)
    lobby_tasks[chat_id] = asyncio.create_task(_lobby_timeout(chat_id, bot))

def cancel_lobby_timeout(chat_id):
    task = lobby_tasks.pop(chat_id, None)
    if task:
        task.cancel()

def game_button(text, url, use_webapp, chat_type):
    if use_webapp and chat_type not in ("group", "supergroup"):
        return {"text": text, "web_app": {"url": url}}
    return {"text": text, "url": url}

def lobby_markup():
    return {"inline_keyboard": [
        [{"text": "✅ Join", "callback_data": "oth_join"},
         {"text": "❌ Leave", "callback_data": "oth_leave"}]
    ]}

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    parts = text.split()
    if len(parts) >= 2 and parts[1].startswith("othello_"):
        gid = parts[1][8:]
        g = othello_game.games.get(gid)
        if not g:
            await update.message.reply_text("❌ Game not found or already finished.")
            return
        url = f"{config.WEBHOOK_URL}/tello?game_id={gid}"
        btn = game_button("⚫ Play Othello", url, True, "private")
        await update.message.reply_text(
            f"⚫ Othello Game\n\n{g['black']['name']} (●) vs {g['white']['name']} (○)\n\nClick to play:",
            api_kwargs={"reply_markup": {"inline_keyboard": [[btn]]}}
        )
    else:
        await update.message.reply_text(
            "🎮 **Game Hub**\n\nUse /game to see available games."
        )

async def game_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        base = config.WEBHOOK_URL
        ct = update.effective_chat.type
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="🎮 **Game Hub**\n\nPick a game:",
            api_kwargs={
                "reply_markup": {
                    "inline_keyboard": [[
                        game_button("🐍 Snake", f"{base}/game", True, ct)
                    ], [
                        {"text": "⚫ Othello Lobby", "callback_data": "oth_hub"}
                    ]]
                }
            }
        )
    except Exception as e:
        logging.error(f"Game command error: {e}")
        await update.message.reply_text(f"❌ Error: {e}")

async def tello_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = update.effective_chat.id
        uid = str(update.effective_user.id)
        name = update.effective_user.first_name or "Player"

        for gid, g in list(othello_game.games.items()):
            if g['black']['id'] == uid or g['white']['id'] == uid:
                url = f"{config.WEBHOOK_URL}/tello?game_id={gid}"
                btn = game_button("⚫ Continue Game", url, True, "private")
                await context.bot.send_message(
                    chat_id=chat_id, text="⚫ You have an active Othello game!",
                    api_kwargs={"reply_markup": {"inline_keyboard": [[btn]]}}
                )
                return

        othello_game.get_or_create_lobby(chat_id)
        schedule_lobby_timeout(chat_id, context.bot)
        text = othello_game.lobby_text(chat_id)
        msg = await context.bot.send_message(
            chat_id=chat_id, text=text,
            api_kwargs={"reply_markup": lobby_markup()}
        )
    except Exception as e:
        logging.error(f"Tello command error: {e}")
        await update.message.reply_text(f"❌ Error: {e}")

async def othello_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    uid = str(user.id)
    name = user.first_name or "Player"
    chat_id = update.effective_chat.id
    data = query.data

    if data == "oth_hub":
        othello_game.get_or_create_lobby(chat_id)
        schedule_lobby_timeout(chat_id, context.bot)
        text = othello_game.lobby_text(chat_id)
        try:
            await query.edit_message_text(
                text=text,
                api_kwargs={"reply_markup": lobby_markup()}
            )
        except Exception:
            await context.bot.send_message(
                chat_id=chat_id, text=text,
                api_kwargs={"reply_markup": lobby_markup()}
            )
        return

    if data == "oth_join":
        for gid, g in list(othello_game.games.items()):
            if g['black']['id'] == uid or g['white']['id'] == uid:
                return

        ok, err = othello_game.lobby_add(chat_id, uid, name)
        # Reset timeout on each join
        schedule_lobby_timeout(chat_id, context.bot)
        text = othello_game.lobby_text(chat_id)
        try:
            await query.edit_message_text(
                text=text,
                api_kwargs={"reply_markup": lobby_markup()}
            )
        except Exception:
            await context.bot.send_message(
                chat_id=chat_id, text=text,
                api_kwargs={"reply_markup": lobby_markup()}
            )
        if not ok:
            return

        await database.save_othello_lobby(chat_id, othello_game.lobbies.get(chat_id, {}).get('players', []))

        gid = await othello_game.check_match(chat_id)
        if gid:
            # Lobby stays active for more games
            await database.save_othello_lobby(chat_id, othello_game.lobbies.get(chat_id, {}).get('players', []))
            g = othello_game.games[gid]
            deep_link = f"https://t.me/{BOT_USERNAME}?start=othello_{gid}"

            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⚫ **Othello Started!**\n\n{g['black']['name']} (●) vs {g['white']['name']} (○)\n\nClick to enter the game (opens in private chat):",
                api_kwargs={
                    "reply_markup": {"inline_keyboard": [[
                        {"text": "⚫ Play Othello", "url": deep_link}
                    ]]}
                }
            )

    elif data == "oth_leave":
        othello_game.lobby_remove(chat_id, uid)
        lobby = othello_game.lobbies.get(chat_id)
        if not lobby or not lobby['players']:
            cancel_lobby_timeout(chat_id)
            await database.delete_othello_lobby(chat_id)
        else:
            await database.save_othello_lobby(chat_id, lobby['players'])
        text = othello_game.lobby_text(chat_id)
        try:
            await query.edit_message_text(
                text=text,
                api_kwargs={"reply_markup": lobby_markup()}
            )
        except Exception:
            await context.bot.send_message(
                chat_id=chat_id, text=text,
                api_kwargs={"reply_markup": lobby_markup()}
            )

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
othello_callback_handler = CallbackQueryHandler(othello_callback, pattern="^oth_")
leaderboard_handler = CommandHandler("leaderboard", leaderboard_command)
start_handler = CommandHandler("start", start_command)
