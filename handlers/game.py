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

        # Check if in an active game
        for gid, g in list(othello_game.games.items()):
            if g['black']['id'] == uid or g['white']['id'] == uid:
                url = f"{config.WEBHOOK_URL}/tello?game_id={gid}"
                btn = game_button("⚫ Continue Game", url, True, "private")
                await context.bot.send_message(
                    chat_id=chat_id, text="⚫ You have an active Othello game!",
                    api_kwargs={"reply_markup": {"inline_keyboard": [[btn]]}}
                )
                return

        # Show lobby
        await send_or_update_lobby(context, chat_id)
    except Exception as e:
        logging.error(f"Tello command error: {e}")
        await update.message.reply_text(f"❌ Error: {e}")

async def send_or_update_lobby(context, chat_id):
    lobby = othello_game.get_or_create_lobby(chat_id)
    text = othello_game.lobby_text(chat_id)
    buttons = othello_game.lobby_buttons()

    if lobby['message_id']:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=lobby['message_id'],
                text=text,
                api_kwargs={"reply_markup": {"inline_keyboard": buttons}}
            )
            return
        except Exception:
            lobby['message_id'] = None

    msg = await context.bot.send_message(
        chat_id=chat_id, text=text,
        api_kwargs={"reply_markup": {"inline_keyboard": buttons}}
    )
    lobby['message_id'] = msg.message_id

async def othello_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    uid = str(user.id)
    name = user.first_name or "Player"
    chat_id = update.effective_chat.id
    data = query.data

    if data == "oth_hub":
        # Show lobby from hub
        await send_or_update_lobby(context, chat_id)
        return

    if data == "oth_join":
        # Check if already in a running game
        for gid, g in list(othello_game.games.items()):
            if g['black']['id'] == uid or g['white']['id'] == uid:
                await send_or_update_lobby(context, chat_id)
                return

        ok, err = othello_game.lobby_add(chat_id, uid, name)
        if not ok:
            await send_or_update_lobby(context, chat_id)
            return

        # Update lobby
        await send_or_update_lobby(context, chat_id)

        # Check for match
        gid = othello_game.check_match(chat_id)
        if gid:
            g = othello_game.games[gid]
            url = f"{config.WEBHOOK_URL}/tello?game_id={gid}"
            p1, p2 = g['black'], g['white']

            # Notify the group (spectators)
            watch_url = f"{config.WEBHOOK_URL}/tello?game_id={gid}"
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⚫ **Othello Started!**\n\n{p1['name']} (●) vs {p2['name']} (○)\n\nClick to watch the game:",
                api_kwargs={
                    "reply_markup": {"inline_keyboard": [[
                        {"text": "👀 Watch Game", "url": watch_url}
                    ]]}
                }
            )

            # Notify each player privately
            for player, opp_name, sym, color in [
                (p1, p2['name'], "○", "Black"),
                (p2, p1['name'], "●", "White"),
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
        await send_or_update_lobby(context, chat_id)

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