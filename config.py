# config.py
import os

# اطلاعات ربات و وب‌هووک
TOKEN = os.environ.get("BOT_TOKEN", "8533985739:AAGd3qNg1F51Vnv-W1K0hk8Vuig6DU_7tck")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://my-telegram-bot-1-gg31.onrender.com")

# اطلاعات دیتابیس پستگرس سوپابیس
DB_URI = os.environ.get("DATABASE_URL", "postgresql://postgres.fzgumhzvhskxkpgiwxvs:Shadow_senpai1388@aws-0-eu-central-1.pooler.supabase.com:6543/postgres")

# شناسه ادمین اصلی
ADMIN_ID = int(os.environ.get("ADMIN_ID", "6630815807"))
