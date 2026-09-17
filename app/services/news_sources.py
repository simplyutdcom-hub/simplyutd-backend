"""Admin-managed RSS feed list.

The ingestion service pulls whatever feeds are ``enabled`` in the
``news_sources`` collection. The operator-facing defaults come from
``RSS_FEEDS`` plus the built-in Google News queries; they are installed once so
that later edits (rename, disable, remove) are never overwritten.
"""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import parse_qs, urlparse

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from .. import db as db_module
from ..config import settings
from ..schemas import NewsSourceCreate, NewsSourceUpdate
from ..utils import new_id, serialize, utcnow
from .rss import GOOGLE_NEWS_QUERIES

logger = logging.getLogger("simplyutd.news_sources")

# Friendly names for the feeds shipped in the default configuration.
_HOST_LABELS = {
    "feeds.bbci.co.uk": "BBC Sport",
    "www.theguardian.com": "The Guardian",
    "www.skysports.com": "Sky Sports",
    "www.manchestereveningnews.co.uk": "Manchester Evening News",
    "www.90min.com": "90min",
    "www.espn.co.uk": "ESPN",
    "news.google.com": "Google News",
}


def _label(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return _HOST_LABELS.get(host) or host.replace("www.", "") or "RSS feed"


def _google_news_label(url: str) -> str:
    """Google News feeds all share a host, so name them after their search."""
    query = parse_qs(urlparse(url).query).get("q", [""])[0].replace('"', "").strip()
    return f"Google News · {query}" if query else "Google News"


def default_sources() -> list[dict[str, str]]:
    """The feed list a fresh install starts with."""
    sources = [{"name": _label(url), "url": url} for url in settings.rss_feeds]
    sources.extend({"name": _google_news_label(url), "url": url} for url in GOOGLE_NEWS_QUERIES)
    return sources


def _collection(database: Database):
    return database[db_module.NEWS_SOURCES]


def list_sources(database: Database) -> list[dict[str, Any]]:
    """Every configured source, built-ins first, then alphabetically."""
    cursor = _collection(database).find().sort([("builtin", DESCENDING), ("name", ASCENDING)])
    return [{k: v for k, v in doc.items() if k != "_id"} for doc in cursor]


def get_source(database: Database, source_id: str) -> dict[str, Any] | None:
    return serialize(_collection(database).find_one({"id": source_id}))


def seed_defaults(database: Database) -> int:
    """Install the defaults on a fresh install (no-op once anything exists)."""
    return _insert_defaults(database, only_missing=False)


def restore_defaults(database: Database) -> int:
    """Re-add any default feed the admin previously removed."""
    return _insert_defaults(database, only_missing=True)


def _insert_defaults(database: Database, *, only_missing: bool) -> int:
    collection = _collection(database)
    if not only_missing and collection.count_documents({}) > 0:
        return 0
    now = utcnow()
    docs = [
        {
            "id": new_id(),
            "name": source["name"],
            "url": source["url"],
            "enabled": True,
            "builtin": True,
            "created_at": now,
            "updated_at": now,
        }
        for source in default_sources()
    ]
    if only_missing:
        existing = {doc["url"] for doc in collection.find({}, {"url": 1})}
        docs = [doc for doc in docs if doc["url"] not in existing]
    if not docs:
        return 0
    try:
        collection.insert_many(docs, ordered=False)
    except DuplicateKeyError:
        # A concurrent startup inserted the same URLs first — harmless.
        logger.debug("Default news sources already present", exc_info=True)
        return 0
    logger.info("Installed %d default news source(s)", len(docs))
    return len(docs)


def enabled_urls(database: Database) -> list[str]:
    """Feed URLs ingestion should pull. Empty means every source is switched off."""
    cursor = _collection(database).find({"enabled": True}, {"url": 1, "name": 1}).sort([("created_at", ASCENDING)])
    return [doc["url"] for doc in cursor if doc.get("url")]


def create_source(database: Database, payload: NewsSourceCreate) -> dict[str, Any]:
    """Add a source. Raises ``DuplicateKeyError`` when the URL already exists."""
    now = utcnow()
    doc = {
        "id": new_id(),
        "name": payload.name.strip(),
        "url": payload.url.strip(),
        "enabled": payload.enabled,
        "builtin": False,
        "created_at": now,
        "updated_at": now,
    }
    _collection(database).insert_one(doc)
    return serialize(doc)  # type: ignore[return-value]


def update_source(
    database: Database, source_id: str, payload: NewsSourceUpdate
) -> dict[str, Any] | None:
    changes: dict[str, Any] = {}
    if payload.name is not None:
        changes["name"] = payload.name.strip()
    if payload.url is not None:
        changes["url"] = payload.url.strip()
    if payload.enabled is not None:
        changes["enabled"] = payload.enabled
    if not changes:
        return get_source(database, source_id)
    changes["updated_at"] = utcnow()
    doc = _collection(database).find_one_and_update(
        {"id": source_id}, {"$set": changes}, return_document=ReturnDocument.AFTER
    )
    return serialize(doc)


def delete_source(database: Database, source_id: str) -> bool:
    return _collection(database).delete_one({"id": source_id}).deleted_count > 0


def count_enabled(database: Database) -> int:
    return _collection(database).count_documents({"enabled": True})


__all__ = [
    "count_enabled",
    "create_source",
    "default_sources",
    "delete_source",
    "enabled_urls",
    "get_source",
    "list_sources",
    "restore_defaults",
    "seed_defaults",
    "update_source",
]
