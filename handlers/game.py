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
                btn = game_button("⚫ Resume Othello Game", url, True, update.effective_chat.type)
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

    # NOTE: Do NOT call query.answer() here unconditionally.
    # Each branch must answer the query exactly once, with the appropriate message.

    if data == "oth_hub":
        await query.answer()
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
            # Answer with the error message as a popup alert — the ONLY answer call for this branch
            await query.answer(err, show_alert=True)
            return

        # Joined successfully — answer with a toast and update the lobby message
        slot_emoji = "⚫" if target_slot == "black" else "⚪"
        await query.answer(f"{slot_emoji} You joined as {target_slot.title()}!", show_alert=False)
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
            await send_match_started_card(context.bot, chat_id, gid, update.effective_chat.type)

    elif data == "oth_vs_ai":
        gid = await othello_game.create_ai_game(uid, name, 'b')
        await query.answer("🤖 Starting match against Othello Bot!", show_alert=True)
        await send_match_started_card(context.bot, chat_id, gid, update.effective_chat.type)

    elif data == "oth_quick":
        gid, opp = await othello_game.join_quick_match(uid, name, chat_id)
        if gid:
            await query.answer("⚡ Match Found! Starting game...", show_alert=True)
            await send_match_started_card(context.bot, chat_id, gid, update.effective_chat.type)
            if opp and opp.get('chat_id') and opp['chat_id'] != chat_id:
                await send_match_started_card(context.bot, opp['chat_id'], gid, 'private')
        else:
            await query.answer("⚡ Added to queue! You'll be notified when matched.", show_alert=True)

    elif data == "oth_leave":
        ok, err = othello_game.lobby_remove(chat_id, uid)
        if not ok:
            await query.answer(err or "You are not in this lobby.", show_alert=True)
            return
        await query.answer("🚪 Left the lobby.")
        text = othello_game.lobby_text(chat_id)
        try:
            await query.edit_message_text(
                text=text, parse_mode="Markdown",
                api_kwargs={"reply_markup": lobby_markup(chat_id)}
            )
        except Exception:
            pass

    elif data == "oth_close":
        await query.answer("🚪 Lobby closed.")
        if chat_id in othello_game.lobbies:
            del othello_game.lobbies[chat_id]
        cancel_lobby_timeout(chat_id)
        await database.delete_othello_lobby(chat_id)
        try:
            await query.edit_message_text("🚪 **Lobby closed.** Use /tello to open a new match arena.", parse_mode="Markdown")
        except Exception:
            pass

    else:
        # Fallback: always answer any unhandled oth_ callback to avoid Telegram timeout
        await query.answer()


async def send_match_started_card(bot, chat_id: int, gid: str, chat_type: str = "private"):
    """Send the match-started card.

    Telegram ONLY allows web_app buttons in private chats.
    In groups/supergroups we must use a url deep-link instead.
    """
    g = othello_game.games.get(gid)
    if not g:
        return
    uname = BOT_USERNAME or getattr(bot, 'username', '') or 'bot'
    deep_link = f"https://t.me/{uname}?start=othello_{gid}"
    web_url = f"{config.WEBHOOK_URL}/tello?game_id={gid}"

    # Choose button type based on chat type:
    # - private chat → web_app (opens Mini App directly)
    # - group/supergroup → url deep-link (opens bot PM first, then Mini App)
    is_private = chat_type == "private"
    if is_private:
        play_btn = {"text": "⚫ Open Game (Mini App)", "web_app": {"url": web_url}}
    else:
        # In groups web_app is not allowed — send deep link that opens the Mini App in PM
        play_btn = {"text": "⚫ Open Game", "url": deep_link}

    buttons = [[play_btn]]
    # In private, also offer a plain URL fallback for browsers without Mini App support
    if is_private:
        buttons[0].append({"text": "🔗 Open in Browser", "url": web_url})

    await bot.send_message(
        chat_id=chat_id,
        text=(
            f"🎉 **Othello Match Started!**\n\n"
            f"⚫ **Black**: {g['black']['name']}\n"
            f"⚪ **White**: {g['white']['name']}\n\n"
            + ("Tap the button to open the game!" if is_private
               else "Tap **Open Game** — it will open in your private chat with the bot.")
        ),
        parse_mode="Markdown",
        api_kwargs={"reply_markup": {"inline_keyboard": buttons}}
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
