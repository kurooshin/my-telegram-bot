import logging
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler
import config
import database
import othello_game

def game_button(text, url, use_webapp, chat_type):
    if use_webapp and chat_type not in ("group", "supergroup"):
        return {"text": text, "web_app": {"url": url}}
    return {"text": text, "url": url}

def lobby_markup():
    return {"inline_keyboard": [
        [{"text": "✅ Join", "callback_data": "oth_join"},
         {"text": "❌ Leave", "callback_data": "oth_leave"}]
    ]}

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
        text = othello_game.lobby_text(chat_id)
        await context.bot.send_message(
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

        gid = othello_game.check_match(chat_id)
        if gid:
            g = othello_game.games[gid]
            url = f"{config.WEBHOOK_URL}/tello?game_id={gid}"

            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⚫ **Othello Started!**\n\n{g['black']['name']} (●) vs {g['white']['name']} (○)\n\nClick to watch:",
                api_kwargs={
                    "reply_markup": {"inline_keyboard": [[
                        {"text": "👀 Watch", "url": url}
                    ]]}
                }
            )

            for player, opp_name, sym, color in [
                (g['black'], g['white']['name'], "○", "Black"),
                (g['white'], g['black']['name'], "●", "White"),
            ]:
                try:
                    await context.bot.send_message(
                        chat_id=player['id'],
                        text=f"⚫ **Match Found!**\n\n{opp_name} ({sym}) vs **You** ({color})\n\nClick to play:",
                        api_kwargs={
                            "reply_markup": {"inline_keyboard": [[
                                game_button("⚫ Play Othello", url, True, "private")
                            ]]}
                        }
                    )
                except Exception as e:
                    logging.error(f"Failed to notify {player['id']}: {e}")

    elif data == "oth_leave":
        othello_game.lobby_remove(chat_id, uid)
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