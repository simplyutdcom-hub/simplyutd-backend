import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# Where the SQLite database file is stored.
DB_PATH = BASE_DIR / "waitlist.db"

# Gmail SMTP settings (loaded from backend/.env).
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_EMAIL = os.getenv("SMTP_EMAIL", "").strip()
SMTP_APP_PASSWORD = os.getenv("SMTP_APP_PASSWORD", "").strip()
NOTIFY_TO = os.getenv("NOTIFY_TO", "").strip() or SMTP_EMAIL

# MongoDB (used when MONGO_URI is set, e.g. MongoDB Atlas or Render's MongoDB).
# When MONGO_URI is empty the app falls back to local SQLite for easy dev.
MONGO_URI = os.getenv("MONGO_URI", "").strip()
MONGO_DB = os.getenv("MONGO_DB", "").strip() or "simplyutd"
MONGO_COLLECTION = "waitlist"
