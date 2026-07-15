# main.py
import os
import asyncio
import logging
from telegram.ext import Application
import config
import database
from handlers.admin_panel import panel_conversation
from handlers.text_responses import keyword_handler
from handlers.say_command import say_handler

# پیکربندی لاگر برای خطایابی در رندر
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)

def main():
    # ۱. ایجاد یک حلقه رویداد پایدار برای پشتیبانی از Python 3.14
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # ۲. اجرای ساختار اولیه دیتابیس
    loop.run_until_complete(database.init_db())

    # ۳. ساخت و پیکربندی اپلیکیشن ربات
    application = Application.builder().token(config.TOKEN).build()

    # ۴. ثبت هندلرها (ترتیب اهمیت دارد: ابتدا منوی مدیریت سپس هندلر متون عمومی)
    application.add_handler(panel_conversation)
    application.add_handler(say_handler)
    application.add_handler(keyword_handler)

    # ۵. دریافت پورت اختصاص داده شده توسط وب‌سرویس Render
    port_number = int(os.environ.get("PORT", 8080))
    
    print("Bot setup completed. Launching Webhook...")

    # ۶. اجرای نهایی تحت وب‌هووک هماهنگ با رندر
    application.run_webhook(
        listen="0.0.0.0",
        port=port_number,
        url_path=config.TOKEN,
        webhook_url=f"{config.WEBHOOK_URL}/{config.TOKEN}"
    )

    loop.close()

if __name__ == '__main__':
    main()
