"""MongoDB access layer.

A single ``MongoClient`` is shared process-wide. Endpoints are synchronous
``def`` functions so FastAPI runs them in a threadpool — pymongo's blocking
driver is therefore safe and simple to use.

``set_db`` allows the test suite (or any caller) to inject an alternate
database handle, e.g. a ``mongomock`` database.
"""
from __future__ import annotations

import logging
from typing import Any

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.database import Database

from .config import settings

logger = logging.getLogger("simplyutd.db")

_client: MongoClient | None = None
_db: Database | None = None

# Collection names referenced across the app.
NEWS = "news"
PRODUCTS = "products"
MESSAGES = "messages"
SUBSCRIBERS = "subscribers"
HUB = "hub"
EVENTS = "events"
META = "meta"
COMMENTS = "comments"
USERS = "users"
SESSIONS = "sessions"


def connect() -> Database:
    """Create the shared client/database and ensure indexes exist."""
    global _client, _db
    if _db is not None:
        return _db
    if settings.use_mongomock:
        import mongomock

        logger.warning(
            "USE_MONGOMOCK is enabled — using an in-memory database (%s). "
            "Data will NOT persist across restarts.",
            settings.mongo_db,
        )
        _db = mongomock.MongoClient(tz_aware=True)[settings.mongo_db]
        ensure_indexes(_db)
        return _db
    logger.info("Connecting to MongoDB (%s / %s)", settings.mongo_uri, settings.mongo_db)
    _client = MongoClient(
        settings.mongo_uri,
        tz_aware=True,
        serverSelectionTimeoutMS=5000,
        uuidRepresentation="standard",
    )
    _db = _client[settings.mongo_db]
    ensure_indexes(_db)
    return _db


def set_db(database: Database) -> None:
    """Override the database handle (used by tests with mongomock)."""
    global _db
    _db = database
    try:
        ensure_indexes(database)
    except Exception:  # noqa: BLE001 - mongomock tolerates most, but be safe
        logger.debug("Skipped index creation for injected db", exc_info=True)


def get_db() -> Database:
    """FastAPI dependency returning the active database handle."""
    if _db is None:
        return connect()
    return _db


def close() -> None:
    """Close the shared client (called from the app lifespan shutdown)."""
    global _client, _db
    if _client is not None:
        _client.close()
    _client = None
    _db = None


def ensure_indexes(db: Database) -> None:
    """Create the indexes required for lookups and de-duplication."""

    def _safe(collection: str, keys: list[tuple[str, int]], **kwargs: Any) -> None:
        try:
            db[collection].create_index(keys, **kwargs)
        except Exception:  # noqa: BLE001 - index failures must not crash startup
            logger.debug("Index creation skipped for %s %s", collection, keys, exc_info=True)

    _safe(NEWS, [("slug", ASCENDING)], unique=True, sparse=True)
    _safe(NEWS, [("guid", ASCENDING)], unique=True, sparse=True)
    _safe(NEWS, [("source_url", ASCENDING)], unique=True, sparse=True)
    _safe(NEWS, [("published_at", DESCENDING)])
    _safe(NEWS, [("status", ASCENDING), ("published_at", DESCENDING)])
    _safe(NEWS, [("category", ASCENDING)])

    _safe(PRODUCTS, [("slug", ASCENDING)], unique=True, sparse=True)
    _safe(PRODUCTS, [("category", ASCENDING)])

    _safe(MESSAGES, [("created_at", DESCENDING)])
    _safe(MESSAGES, [("unread", ASCENDING)])

    _safe(SUBSCRIBERS, [("email", ASCENDING)], unique=True)

    _safe(EVENTS, [("created_at", DESCENDING)])
    _safe(EVENTS, [("type", ASCENDING), ("created_at", DESCENDING)])

    _safe(HUB, [("key", ASCENDING)], unique=True)
    _safe(META, [("key", ASCENDING)], unique=True)

    _safe(COMMENTS, [("id", ASCENDING)], unique=True)
    _safe(COMMENTS, [("target_type", ASCENDING), ("target_id", ASCENDING), ("created_at", DESCENDING)])
    _safe(COMMENTS, [("parent_id", ASCENDING)])

    _safe(USERS, [("id", ASCENDING)], unique=True)
    _safe(USERS, [("email", ASCENDING)], unique=True)

    _safe(SESSIONS, [("token", ASCENDING)], unique=True)
    _safe(SESSIONS, [("user_id", ASCENDING)])
    _safe(SESSIONS, [("expires_at", ASCENDING)])
