"""Handles all non-command text messages — group tracking, keyword matching, and AI replies."""

import logging
from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters
import database
import ai_service

logger = logging.getLogger(__name__)


async def _keyword_fallback(update: Update, incoming_text: str) -> bool:
    """Try keyword matching. Returns True if a reply was sent."""
    pool = await database.get_pool()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow('''
                SELECT response FROM bot_keywords WHERE
                    (match_type = 'exact' AND LOWER(keyword) = LOWER($1))
                    OR
                    (match_type = 'flexible' AND POSITION(LOWER(keyword) IN LOWER($1)) > 0)
                ORDER BY
                    CASE WHEN match_type = 'exact' THEN 0 ELSE 1 END,
                    LENGTH(keyword) DESC
                LIMIT 1
            ''', incoming_text)
            if row:
                await update.message.reply_text(row['response'])
                return True
    except Exception as e:
        logger.error("Keyword matching error: %s", e, exc_info=True)
    return False


async def _is_bot_mentioned(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if the message @mentions the bot or replies to a bot message."""
    msg = update.message
    if not msg:
        return False

    # Condition 3: reply to the bot's own message
    if msg.reply_to_message and msg.reply_to_message.from_user:
        if msg.reply_to_message.from_user.id == context.bot.id:
            return True

    if not msg.text:
        return False

    raw = msg.text.strip()

    # Condition 1: @bot_username in message
    bot_username = (context.bot.username or '').lower()
    if bot_username and f'@{bot_username}' in raw.lower():
        return True

    # Condition 2: custom trigger word
    try:
        trigger = database.normalize_persian(await database.get_trigger_word()).strip()
        if trigger and trigger in database.normalize_persian(raw):
            return True
    except Exception:
        pass

    return False


def _strip_trigger(raw: str, trigger: str, bot_username: str) -> str:
    """Remove the trigger word/mention from the beginning of a message."""
    text = raw.strip()
    # Remove @bot_username (case-insensitive)
    if bot_username:
        mention = f'@{bot_username}'
        idx = text.lower().find(mention)
        if idx != -1:
            text = text[:idx] + text[idx + len(mention):]
    # Remove trigger word from start
    if trigger:
        normalized = database.normalize_persian(text)
        if normalized.startswith(database.normalize_persian(trigger)):
            text = text[len(trigger):]
    return text.strip()


async def monitor_keywords(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    chat = update.effective_chat
    raw_text = update.message.text.strip()
    incoming_text = database.normalize_persian(raw_text)

    if chat.type in ("group", "supergroup"):
        if not await _is_bot_mentioned(update, context):
            return  # silent ignore — bot was not addressed

        # Strip trigger before further processing
        try:
            trigger = database.normalize_persian(await database.get_trigger_word()).strip()
        except Exception:
            trigger = 'بات'
        bot_username = context.bot.username or ''
        stripped = _strip_trigger(raw_text, trigger, bot_username)
        if stripped:
            incoming_text = database.normalize_persian(stripped)

    logger.info(
        "[MONITOR] Text received: chat_id=%s chat_type=%s text='%s' user_id=%s",
        chat.id, chat.type, incoming_text[:80], update.effective_user.id,
    )

    pool = await database.get_pool()
    async with pool.acquire() as conn:
        if chat.type in ("group", "supergroup"):
            try:
                await conn.execute('''
                    INSERT INTO bot_groups (group_id, title)
                    VALUES ($1, $2)
                    ON CONFLICT (group_id) DO UPDATE SET title = $2, updated_at = CURRENT_TIMESTAMP
                ''', chat.id, chat.title)
                is_active = await conn.fetchval('SELECT is_active FROM bot_groups WHERE group_id = $1', chat.id)
                if is_active is False:
                    logger.info("[MONITOR] Group %s is inactive — ignoring message", chat.id)
                    return
            except Exception as e:
                logger.warning("Group tracking failed for %s: %s", chat.id, e)

    # Step 1: Check AI status
    ai_enabled = await database.is_ai_enabled(chat.id)
    logger.info("[MONITOR] AI status for chat_id=%s: enabled=%s", chat.id, ai_enabled)

    if ai_enabled:
        logger.info("[MONITOR] AI path: calling Groq for chat_id=%s", chat.id)
        try:
            history = await database.get_chat_history(chat.id, limit=6)
            known_facts = await database.get_all_keywords()
            persona = await database.get_bot_persona()
            reply = await ai_service.get_ai_reply(
                incoming_text, history=history, known_facts=known_facts, persona=persona
            )
            logger.info(
                "[MONITOR] Groq raw response for chat_id=%s: %s",
                chat.id, "None" if reply is None else f"'{reply[:100]}'",
            )
            if reply:
                await update.message.reply_text(reply)
                await database.save_chat_turn(chat.id, incoming_text, reply, keep_last=6)
                logger.info("[MONITOR] AI reply sent to chat_id=%s (len=%s)", chat.id, len(reply))
                return
        except Exception as e:
            logger.error("[MONITOR] AI reply exception for chat_id=%s: %s", chat.id, e, exc_info=True)

        # AI enabled but no reply (rate limit / error) — no keyword fallback
        await database.log_unmatched(incoming_text, chat.id)
        logger.info("[MONITOR] AI enabled but no reply (no keyword fallback) — chat_id=%s text='%s'", chat.id, incoming_text[:60])
        return

    # Step 2: AI disabled — keyword matching only
    kw_matched = await _keyword_fallback(update, incoming_text)
    logger.info("[MONITOR] Keyword-only path for chat_id=%s: matched=%s", chat.id, kw_matched)
    if kw_matched:
        return
    await database.log_unmatched(incoming_text, chat.id)
    logger.info("[MONITOR] Unmatched (no-AI path) — chat_id=%s text='%s'", chat.id, incoming_text[:60])


keyword_handler = MessageHandler(filters.TEXT & ~filters.COMMAND, monitor_keywords)
