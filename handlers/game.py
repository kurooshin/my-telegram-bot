import asyncio
import logging
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler
import config
import database
import othello_game

logger = logging.getLogger(__name__)

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
        logger.error(f"Lobby timeout error: {e}")


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


def lobby_markup(chat_id: int | None = None):
    return {"inline_keyboard": othello_game.lobby_buttons(chat_id)}


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
            f"⚫ **Othello Game**\n\n{g['black']['name']} (●) vs {g['white']['name']} (○)\n\nClick below to launch:",
            parse_mode="Markdown",
            api_kwargs={"reply_markup": {"inline_keyboard": [[btn]]}}
        )
    else:
        await update.message.reply_text(
            "🎮 **Game Hub**\n\nUse /game to see available games, or /tello to join Othello!",
            parse_mode="Markdown"
        )


async def game_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        base = config.WEBHOOK_URL
        ct = update.effective_chat.type
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="🎮 **Game Hub**\n\nPick a game to play:",
            parse_mode="Markdown",
            api_kwargs={
                "reply_markup": {
                    "inline_keyboard": [[
                        game_button("🐍 Snake Pro", f"{base}/game", True, ct)
                    ], [
                        {"text": "⚫⚪ Othello Match Arena", "callback_data": "oth_hub"}
                    ]]
                }
            }
        )
    except Exception as e:
        logger.error(f"Game command error: {e}")
        await update.message.reply_text("❌ Could not open game hub. Try again later.")


async def tello_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = update.effective_chat.id
        uid = str(update.effective_user.id)

        for gid, g in list(othello_game.games.items()):
            if not g['game_over'] and (g['black']['id'] == uid or g['white']['id'] == uid):
                url = f"{config.WEBHOOK_URL}/tello?game_id={gid}"
                btn = game_button("⚫ Resume Othello Game", url, True, "private")
                await context.bot.send_message(
                    chat_id=chat_id, text="⚫ You have an active Othello game!",
                    api_kwargs={"reply_markup": {"inline_keyboard": [[btn]]}}
                )
                return

        othello_game.get_or_create_lobby(chat_id)
        schedule_lobby_timeout(chat_id, context.bot)
        text = othello_game.lobby_text(chat_id)
        await context.bot.send_message(
            chat_id=chat_id, text=text, parse_mode="Markdown",
            api_kwargs={"reply_markup": lobby_markup(chat_id)}
        )
    except Exception as e:
        logger.error(f"Tello command error: {e}")
        await update.message.reply_text("❌ Could not create lobby. Try again later.")


async def othello_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    uid = str(user.id)
    name = user.first_name or "Player"
    chat_id = update.effective_chat.id
    data = query.data

    await query.answer()

    if data == "oth_hub":
        othello_game.get_or_create_lobby(chat_id)
        schedule_lobby_timeout(chat_id, context.bot)
        text = othello_game.lobby_text(chat_id)
        try:
            await query.edit_message_text(
                text=text, parse_mode="Markdown",
                api_kwargs={"reply_markup": lobby_markup(chat_id)}
            )
        except Exception:
            await context.bot.send_message(
                chat_id=chat_id, text=text, parse_mode="Markdown",
                api_kwargs={"reply_markup": lobby_markup(chat_id)}
            )
        return

    if data in ("oth_slot_b", "oth_slot_w", "oth_join"):
        target_slot = 'black' if data == "oth_slot_b" else ('white' if data == "oth_slot_w" else 'black')
        ok, err = othello_game.lobby_join_slot(chat_id, uid, name, target_slot)
        schedule_lobby_timeout(chat_id, context.bot)

        if not ok and err:
            try:
                await query.answer(err, show_alert=True)
            except Exception:
                pass
            return

        text = othello_game.lobby_text(chat_id)
        try:
            await query.edit_message_text(
                text=text, parse_mode="Markdown",
                api_kwargs={"reply_markup": lobby_markup(chat_id)}
            )
        except Exception:
            pass

        gid = await othello_game.check_match(chat_id)
        if gid:
            await send_match_started_card(context.bot, chat_id, gid)

    elif data == "oth_vs_ai":
        gid = await othello_game.create_ai_game(uid, name, 'b')
        await query.answer("🤖 Created match against Othello Bot!", show_alert=True)
        await send_match_started_card(context.bot, chat_id, gid)

    elif data == "oth_quick":
        gid, opp = await othello_game.join_quick_match(uid, name, chat_id)
        if gid:
            await query.answer("⚡ Match Found!", show_alert=True)
            await send_match_started_card(context.bot, chat_id, gid)
            if opp and opp.get('chat_id') and opp['chat_id'] != chat_id:
                await send_match_started_card(context.bot, opp['chat_id'], gid)
        else:
            await query.answer("⚡ Searching for an opponent... You'll be notified when matched!", show_alert=True)

    elif data == "oth_leave":
        ok, err = othello_game.lobby_remove(chat_id, uid)
        text = othello_game.lobby_text(chat_id)
        try:
            await query.edit_message_text(
                text=text, parse_mode="Markdown",
                api_kwargs={"reply_markup": lobby_markup(chat_id)}
            )
        except Exception:
            pass

    elif data == "oth_close":
        if chat_id in othello_game.lobbies:
            del othello_game.lobbies[chat_id]
        cancel_lobby_timeout(chat_id)
        await database.delete_othello_lobby(chat_id)
        try:
            await query.edit_message_text("🚪 **Lobby closed.** Use /tello to open a new match arena.", parse_mode="Markdown")
        except Exception:
            pass


async def send_match_started_card(bot, chat_id: int, gid: str):
    g = othello_game.games.get(gid)
    if not g:
        return
    uname = BOT_USERNAME or getattr(bot, 'username', '') or 'bot'
    deep_link = f"https://t.me/{uname}?start=othello_{gid}"
    web_url = f"{config.WEBHOOK_URL}/tello?game_id={gid}"

    await bot.send_message(
        chat_id=chat_id,
        text=(
            f"🎉 **Othello Match Started!**\n\n"
            f"• **Black (●)**: {g['black']['name']}\n"
            f"• **White (○)**: {g['white']['name']}\n\n"
            f"Click below to launch the game:"
        ),
        parse_mode="Markdown",
        api_kwargs={
            "reply_markup": {"inline_keyboard": [[
                {"text": "⚫ Play Othello (App)", "web_app": {"url": web_url}},
                {"text": "🔗 Play Direct Link", "url": deep_link}
            ]]}
        }
    )


async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        rows = await database.get_leaderboard()
        if not rows:
            await update.message.reply_text("🏆 No scores recorded yet. Play Snake and be the first!", parse_mode="Markdown")
            return
        text = "🏆 **Snake Pro Leaderboard**\n\n"
        for i, r in enumerate(rows, 1):
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"*{i}.*")
            name = (r['user_name'] or 'Player')[:20]
            text += f"{medal} **{name}** — `{r['score']}` pts\n"
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Leaderboard error: {e}")
        await update.message.reply_text("❌ Could not load leaderboard.")



game_handler = CommandHandler("game", game_command)
tello_handler = CommandHandler("tello", tello_command)
othello_callback_handler = CallbackQueryHandler(othello_callback, pattern="^oth_")
leaderboard_handler = CommandHandler("leaderboard", leaderboard_command)
start_handler = CommandHandler("start", start_command)
