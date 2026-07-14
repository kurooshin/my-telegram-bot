# config.py
import os

# اطلاعات ربات و وب‌هووک
TOKEN = os.environ.get("BOT_TOKEN", "8533985739:AAHeWARNevU2wIuHiV3uzuJuwnQFYjXP2bc")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://my-telegram-bot-w0zy.onrender.com")

# اطلاعات دیتابیس پستگرس سوپابیس
DB_URI = os.environ.get("DATABASE_URL", "postgresql://postgres.fzgumhzvhskxkpgiwxvs:Shadow_senpai1388@aws-0-eu-central-1.pooler.supabase.com:6543/postgres")

# شناسه ادمین اصلی
ADMIN_ID = int(os.environ.get("ADMIN_ID", "6630815807"))
