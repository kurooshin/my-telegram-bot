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
        logger.error("Keyword matching error: %s", e)
    return False


async def monitor_keywords(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    chat = update.effective_chat
    incoming_text = database.normalize_persian(update.message.text.strip())

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
                    return
            except Exception as e:
                logger.warning("Group tracking failed for %s: %s", chat.id, e)

    # Step 1: Check AI status
    if await database.is_ai_enabled(chat.id):
        try:
            history = await database.get_chat_history(chat.id, limit=6)
            known_facts = await database.get_all_keywords()
            reply = await ai_service.get_ai_reply(
                incoming_text, history=history, known_facts=known_facts
            )
            if reply:
                await update.message.reply_text(reply)
                await database.save_chat_turn(chat.id, incoming_text, reply, keep_last=6)
                return
        except Exception as e:
            logger.error("AI reply error for %s: %s", chat.id, e)

        # AI returned None (rate limit / error) → fallback to keywords
        if await _keyword_fallback(update, incoming_text):
            return
        await database.log_unmatched(incoming_text, chat.id)
        return

    # Step 2: AI disabled — keyword matching only
    if await _keyword_fallback(update, incoming_text):
        return
    await database.log_unmatched(incoming_text, chat.id)


keyword_handler = MessageHandler(filters.TEXT & ~filters.COMMAND, monitor_keywords)
