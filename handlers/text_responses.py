"""Handles all non-command text messages — group tracking and keyword matching."""

import logging
from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters
import database

logger = logging.getLogger(__name__)


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

        try:
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
        except Exception as e:
            logger.error("Keyword matching error: %s", e)


keyword_handler = MessageHandler(filters.TEXT & ~filters.COMMAND, monitor_keywords)
