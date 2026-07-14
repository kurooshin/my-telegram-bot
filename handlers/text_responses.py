# handlers/text_responses.py
from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters
import database

async def monitor_keywords(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
        
    chat = update.effective_chat
    incoming_text = update.message.text.strip()
    
    conn = await database.get_db_connection()
    try:
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

        # ۲. بررسی کلمات کلیدی و پاسخ زنده (هم برای پی‌وی و هم برای گروه‌های مجاز)
        row = await conn.fetchrow('SELECT response FROM bot_keywords WHERE keyword = $1', incoming_text)
        if row:
            await update.message.reply_text(row['response'])

    finally:
        await conn.close()

# فعال روی تمام پیام‌های متنی به‌جز دستورات
keyword_handler = MessageHandler(filters.TEXT & ~filters.COMMAND, monitor_keywords)
