import os

TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
    raise ValueError("BOT_TOKEN environment variable is required")

WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
if not WEBHOOK_URL:
    raise ValueError("WEBHOOK_URL environment variable is required")

DB_URI = os.environ.get("DATABASE_URL")
if not DB_URI:
    raise ValueError("DATABASE_URL environment variable is required")

ADMIN_ID = int(os.environ["ADMIN_ID"])
