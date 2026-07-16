# handlers/text_responses.py
from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters
import database

async def monitor_keywords(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
        
    chat = update.effective_chat
    incoming_text = database.normalize_persian(update.message.text.strip())
    
    pool = await database.get_pool()
    async with pool.acquire() as conn:
        # ۱. مدیریت پیام‌های ارسالی در گروه‌ها و سوپرگروه‌ها
        if chat.type in ["group", "supergroup"]:
            try:
                await conn.execute('''
                    INSERT INTO bot_groups (group_id, title) 
                    VALUES ($1, $2) 
                    ON CONFLICT (group_id) DO UPDATE SET title = $2, updated_at = CURRENT_TIMESTAMP
                ''', chat.id, chat.title)

                is_active = await conn.fetchval('SELECT is_active FROM bot_groups WHERE group_id = $1', chat.id)

                if is_active is False:
                    return
            except Exception:
                pass

        # ۲. بررسی کلمات کلیدی (exact یا flexible براساس match_type)
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

# فعال روی تمام پیام‌های متنی به‌جز دستورات
keyword_handler = MessageHandler(filters.TEXT & ~filters.COMMAND, monitor_keywords)
