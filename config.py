import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# Where the SQLite database file is stored.
DB_PATH = BASE_DIR / "waitlist.db"

# Email delivery via Resend (HTTPS API — works on Render's free tier,
# which blocks SMTP ports 25/465/587). Settings loaded from backend/.env.
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
# Verified "From" address, e.g. "SimplyUtd <no-reply@simplyutd.com>" once you
# add a domain, or "SimplyUtd <onboarding@resend.dev>" for testing to your own
# inbox. Resend requires "From" to be a verified domain/address.
RESEND_FROM = os.getenv("RESEND_FROM", "SimplyUtd <onboarding@resend.dev>").strip()
# Inbox that receives the "new signup" notifications to the site owner.
NOTIFY_TO = os.getenv("NOTIFY_TO", "").strip()
RESEND_API_URL = "https://api.resend.com/emails"

# MongoDB (used when MONGO_URI is set, e.g. MongoDB Atlas or Render's MongoDB).
# When MONGO_URI is empty the app falls back to local SQLite for easy dev.
MONGO_URI = os.getenv("MONGO_URI", "").strip()
MONGO_DB = os.getenv("MONGO_DB", "").strip() or "simplyutd"
MONGO_COLLECTION = "waitlist"
