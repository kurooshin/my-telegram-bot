# main.py
import os
import asyncio
import logging
from telegram.ext import Application
import config
import database
from handlers.admin_panel import panel_conversation
from handlers.text_responses import keyword_handler

# پیکربندی لاگر برای خطایابی در رندر
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)

def main():
    # ۱. اجرای ساختار اولیه دیتابیس به صورت همگام در شروع برنامه
    loop = asyncio.get_event_loop()
    loop.run_until_complete(database.init_db())

    # ۲. ساخت و پیکربندی اپلیکیشن ربات
    application = Application.builder().token(config.TOKEN).build()

    # ۳. ثبت هندلرها (ترتیب اهمیت دارد: ابتدا منوی مدیریت سپس هندلر متون عمومی)
    application.add_handler(panel_conversation)
    application.add_handler(keyword_handler)

    # ۴. دریافت پورت اختصاص داده شده توسط وب‌سرویس Render
    port_number = int(os.environ.get("PORT", 8080))
    
    print("Bot setup completed. Launching Webhook...")

    # ۵. اجرای نهایی تحت وب‌هووک هماهنگ با رندر
    application.run_webhook(
        listen="0.0.0.0",
        port=port_number,
        url_path=config.TOKEN,
        webhook_url=f"{config.WEBHOOK_URL}/{config.TOKEN}"
    )

if __name__ == '__main__':
    main()
