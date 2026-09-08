import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from config import DB_PATH, MONGO_COLLECTION, MONGO_DB, MONGO_URI


# ---------------------------------------------------------------------------
# SQLite backend (local/dev fallback)
# ---------------------------------------------------------------------------

@contextmanager
def get_sqlite_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _sqlite_init() -> None:
    with get_sqlite_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS waitlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            )
            """
        )


def _sqlite_add(email: str) -> tuple[bool, str]:
    created_at = datetime.now(timezone.utc).isoformat()
    try:
        with get_sqlite_connection() as conn:
            conn.execute(
                "INSERT INTO waitlist (email, created_at) VALUES (?, ?)",
                (email, created_at),
            )
        return True, "You're on the list! We'll be in touch soon."
    except sqlite3.IntegrityError:
        return False, "You're already on the list."


# ---------------------------------------------------------------------------
# MongoDB backend (production, e.g. Render / MongoDB Atlas)
# ---------------------------------------------------------------------------

_mongo_client = None


def _get_mongo_collection():
    global _mongo_client
    if _mongo_client is None:
        from pymongo import MongoClient

        _mongo_client = MongoClient(MONGO_URI)
    return _mongo_client[MONGO_DB][MONGO_COLLECTION]


def _mongo_init() -> None:
    collection = _get_mongo_collection()
    # Enforce uniqueness so duplicate signups are rejected at the DB level.
    collection.create_index("email", unique=True)


def _mongo_add(email: str) -> tuple[bool, str]:
    from pymongo.errors import DuplicateKeyError

    created_at = datetime.now(timezone.utc).isoformat()
    collection = _get_mongo_collection()
    try:
        collection.insert_one({"email": email, "created_at": created_at})
        return True, "You're on the list! We'll be in touch soon."
    except DuplicateKeyError:
        return False, "You're already on the list."


# ---------------------------------------------------------------------------
# Public interface — picks a backend based on config.
# ---------------------------------------------------------------------------

def use_mongodb() -> bool:
    return bool(MONGO_URI)


def init_db() -> None:
    if use_mongodb():
        _mongo_init()
    else:
        _sqlite_init()


def add_signup(email: str) -> tuple[bool, str]:
    if use_mongodb():
        return _mongo_add(email)
    return _sqlite_add(email)
