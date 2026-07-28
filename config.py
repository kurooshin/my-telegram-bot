import os

TOKEN = os.environ.get("BOT_TOKEN", "123456789:TEST_BOT_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "http://localhost:8080")
DB_URI = os.environ.get("DATABASE_URL", "postgresql://user:pass@localhost:5432/dbname")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")ABASE_URL", "postgresql://user:pass@localhost:5432/dbname")

_admin_id_raw = os.environ.get("ADMIN_ID", "0")
try:
    ADMIN_ID = int(_admin_id_raw)
except ValueError:
    ADMIN_ID = 0

def check_config():
    """Verify that required environment variables are provided for production execution."""
    missing = []
    if "BOT_TOKEN" not in os.environ:
        missing.append("BOT_TOKEN")
    if "WEBHOOK_URL" not in os.environ:
        missing.append("WEBHOOK_URL")
    if "DATABASE_URL" not in os.environ:
        missing.append("DATABASE_URL")
    if "ADMIN_ID" not in os.environ:
        missing.append("ADMIN_ID")
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

