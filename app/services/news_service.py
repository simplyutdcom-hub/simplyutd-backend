"""News persistence, querying and de-duplication."""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from pymongo import DESCENDING, ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from .. import db as db_module
from ..schemas import NewsCreate, NewsUpdate
from ..utils import new_id, serialize, serialize_many, slugify, utcnow
from . import classify, feed_rank
from .rss import FeedEntry

logger = logging.getLogger("simplyutd.news")


def _unique_slug(database: Database, base: str) -> str:
    slug = slugify(base)
    candidate = slug
    counter = 1
    while database[db_module.NEWS].find_one({"slug": candidate}, {"_id": 1}):
        counter += 1
        candidate = f"{slug}-{counter}"
    return candidate


def list_news(
    database: Database,
    *,
    status: str | None = "Published",
    category: str | None = None,
    featured: bool | None = None,
    q: str | None = None,
    source: str | None = None,
    limit: int = 20,
    skip: int = 0,
    sort: str = "published_at",
    united_only: bool = True,
    min_items: int = 8,
) -> tuple[list[dict[str, Any]], int]:
    """List articles.

    The public surfaces are United-only and ordered by the relevance algorithm
    in :mod:`app.services.feed_rank`; admin listings pass ``united_only=False``
    to see the raw corpus in plain chronological order.

    ``min_items`` is the size the club-gated pool must reach before the ranking
    falls back to the unfiltered one. Callers behind a section filter pass a low
    value so a thin section never has to pad itself with rival-club stories.
    """
    if united_only:
        items, total = feed_rank.fetch(
            database,
            limit=max(1, min(limit, 100)),
            skip=max(0, skip),
            query=q,
            category=category,
            source=source,
            featured=featured,
            status=status or "Published",
            min_items=max(1, min_items),
        )
        return serialize_many(items), total

    query: dict[str, Any] = {}
    if status and status.lower() != "all":
        query["status"] = status
    if category and category.lower() not in {"all", ""}:
        query["category"] = category
    if source:
        query["source"] = source
    if featured is not None:
        query["featured"] = featured
    if q:
        query["$or"] = [
            {"title": {"$regex": q, "$options": "i"}},
            {"summary": {"$regex": q, "$options": "i"}},
            {"source": {"$regex": q, "$options": "i"}},
        ]

    cursor = (
        database[db_module.NEWS]
        .find(query)
        .sort(sort, DESCENDING)
        .skip(max(0, skip))
        .limit(max(1, min(limit, 100)))
    )
    total = database[db_module.NEWS].count_documents(query)
    return serialize_many(list(cursor)), total


def get_news(database: Database, news_id: str) -> dict[str, Any] | None:
    doc = database[db_module.NEWS].find_one({"id": news_id})
    if doc is None:
        doc = database[db_module.NEWS].find_one({"slug": news_id})
    return serialize(doc)


def body_of(doc: dict[str, Any]) -> str:
    """The text shown on an article page: syndicated content, else the summary."""
    return (doc.get("content") or doc.get("summary") or "").strip()


def minutes_ago(value: datetime | None, *, now: datetime | None = None) -> int:
    """Minutes between ``value`` and now, clamped at zero."""
    if not isinstance(value, datetime):
        return 0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    reference = now or utcnow()
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return max(0, int((reference - value).total_seconds() // 60))


def story_card(doc: dict[str, Any]) -> dict[str, Any]:
    """Compact shape used by the "related stories" strips."""
    return {
        "id": doc.get("id"),
        "slug": doc.get("slug"),
        "title": doc.get("title"),
        "summary": doc.get("summary"),
        "image": doc.get("image"),
        "source": doc.get("source"),
        "category": doc.get("category"),
        "published_at": doc.get("published_at"),
        "views": doc.get("views", 0),
    }


def live_item(doc: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Compact shape used by the "Live United" update stream."""
    return {
        "id": doc.get("id"),
        "slug": doc.get("slug"),
        "title": doc.get("title"),
        "description": doc.get("summary") or doc.get("content") or doc.get("title"),
        "comments": doc.get("comments", 0),
        "time": minutes_ago(doc.get("published_at"), now=now),
        "image": doc.get("image"),
        "source": doc.get("source"),
        "source_url": doc.get("source_url"),
        "category": doc.get("category"),
        "published_at": doc.get("published_at"),
    }


def related(
    database: Database,
    doc: dict[str, Any],
    *,
    limit: int = 4,
    shape: str = "story",
) -> list[dict[str, Any]]:
    """Articles related to ``doc``.

    Matching is intentionally loose and best-effort: shared tags first, then the
    same category, then the same source, and finally the newest published
    articles, so a story page always has something to show underneath it.
    """
    base: dict[str, Any] = {
        "status": "Published",
        "id": {"$ne": doc.get("id")},
    }
    tags = [tag for tag in (doc.get("tags") or []) if tag]

    filters: list[dict[str, Any] | None] = [
        {**base, "tags": {"$in": tags}} if tags else None,
        {**base, "category": doc.get("category")} if doc.get("category") else None,
        {**base, "source": doc.get("source")} if doc.get("source") else None,
        base,
    ]

    seen = {doc.get("id")}
    found: list[dict[str, Any]] = []
    for criteria in filters:
        if criteria is None or len(found) >= limit:
            continue
        cursor = (
            database[db_module.NEWS]
            .find(criteria)
            .sort("published_at", DESCENDING)
            .limit(limit * 3)
        )
        for candidate in cursor:
            if candidate.get("id") in seen:
                continue
            seen.add(candidate.get("id"))
            found.append(candidate)
            if len(found) >= limit:
                break

    serialised = serialize_many(found)
    # Related stories should be about the club too. ``min_items=1`` keeps the
    # strip populated when the corpus is small, so a story page never loses its
    # "read next" block to a gate.
    serialised = united_only(serialised, min_items=1)
    if shape == "live":
        return [live_item(item) for item in serialised]
    return [story_card(item) for item in serialised]


def detail(
    database: Database,
    doc: dict[str, Any],
    *,
    limit: int = 4,
    shape: str = "story",
) -> dict[str, Any]:
    """Full article payload for the story/live detail pages."""
    return {
        **doc,
        "body": body_of(doc),
        "related": related(database, doc, limit=limit, shape=shape),
    }


def increment_views(database: Database, news_id: str) -> dict[str, Any] | None:
    doc = database[db_module.NEWS].find_one_and_update(
        {"$or": [{"id": news_id}, {"slug": news_id}]},
        {"$inc": {"views": 1}},
        return_document=ReturnDocument.AFTER,
    )
    return serialize(doc)


def ticker(database: Database, limit: int = 6) -> list[dict[str, Any]]:
    docs, _ = feed_rank.fetch(database, limit=limit, min_items=1, max_per_source=2)
    return [{"id": d.get("id"), "title": d.get("title")} for d in docs]


def ranked(database: Database, *, limit: int = 12, skip: int = 0) -> list[dict[str, Any]]:
    """A page of the United-first feed, used by the home page slices."""
    docs, _ = feed_rank.fetch(database, limit=limit, skip=skip, min_items=1)
    return serialize_many(docs)


def category_counts(database: Database, *, pool_size: int = 1000) -> dict[str, Any]:
    """How many club-relevant stories sit in each section.

    Counted over the gated corpus (never the raw one) so the numbers on the
    section tabs match the lists the tabs actually open. Sections are returned
    in descending size, with the generic "News" bucket always last.
    """
    docs, _ = feed_rank.fetch(
        database,
        limit=max(1, pool_size),
        min_items=1,
        max_per_source=max(1, pool_size),
    )

    counts: Counter[str] = Counter()
    for doc in docs:
        counts[str(doc.get("category") or classify.DEFAULT_CATEGORY)] += 1

    names = sorted(
        counts,
        key=lambda name: (name == classify.DEFAULT_CATEGORY, -counts[name], name),
    )
    return {
        "categories": [{"name": name, "count": counts[name]} for name in names],
        "total": len(docs),
    }


def united_only(docs: list[dict[str, Any]], *, min_items: int = 5) -> list[dict[str, Any]]:
    """Filter an already-ordered pool down to United articles.

    Order is preserved, which is what "trending" needs: the point there is
    engagement rank, not editorial rank. When the pool is too small to afford
    the filter (cold database, quiet news day) the original list is returned so
    the panel is never empty.
    """
    gated = [doc for doc in docs if feed_rank.analyse(doc).united]
    return gated if len(gated) >= min_items else docs


def create_news(database: Database, payload: NewsCreate, *, external: bool = False) -> dict[str, Any]:
    now = utcnow()
    doc = {
        "id": new_id(),
        "slug": _unique_slug(database, payload.title),
        "views": 0,
        "comments": 0,
        "external": external,
        "published_at": now if payload.status == "Published" else None,
        "created_at": now,
        "updated_at": now,
        **payload.model_dump(),
    }
    doc.update(feed_rank.annotate(doc, now=now))
    database[db_module.NEWS].insert_one(doc)
    return serialize(doc)  # type: ignore[return-value]


def update_news(database: Database, news_id: str, payload: NewsUpdate) -> dict[str, Any] | None:
    updates = payload.model_dump(exclude_unset=True, exclude_none=True)
    if not updates:
        return get_news(database, news_id)
    updates["updated_at"] = utcnow()
    if updates.get("status") == "Published":
        existing = database[db_module.NEWS].find_one({"id": news_id}, {"published_at": 1})
        if existing and not existing.get("published_at"):
            updates["published_at"] = utcnow()
    doc = database[db_module.NEWS].find_one_and_update(
        {"$or": [{"id": news_id}, {"slug": news_id}]},
        {"$set": updates},
        return_document=ReturnDocument.AFTER,
    )
    if doc is not None:
        fields = feed_rank.annotate(doc)
        database[db_module.NEWS].update_one({"_id": doc["_id"]}, {"$set": fields})
        doc.update(fields)
    return serialize(doc)


def delete_news(database: Database, news_id: str) -> bool:
    result = database[db_module.NEWS].delete_one({"$or": [{"id": news_id}, {"slug": news_id}]})
    return result.deleted_count > 0


def upsert_entry(database: Database, entry: FeedEntry, *, image_url: str | None = None) -> bool:
    """Insert one RSS entry if it isn't already stored. Returns True if inserted."""
    link = entry.link
    existing = database[db_module.NEWS].find_one(
        {"$or": [{"guid": entry.guid}, {"source_url": link}]}, {"_id": 1}
    )
    if existing:
        return False

    now = utcnow()
    published = entry.published_at or now
    doc = {
        "id": new_id(),
        "slug": _unique_slug(database, entry.title),
        "title": entry.title,
        "summary": entry.summary,
        "content": None,
        "image": image_url or entry.image,
        "source": entry.source,
        "source_url": link,
        "category": entry.category,
        "tags": entry.tags,
        "author": None,
        "status": "Published",
        "featured": False,
        "views": 0,
        "comments": 0,
        "external": True,
        "guid": entry.guid,
        "published_at": published,
        "created_at": now,
        "updated_at": now,
    }
    # Store the relevance verdict so the corpus can be inspected and filtered
    # in Mongo without re-scoring on every request.
    doc.update(feed_rank.annotate(doc, now=now))
    try:
        database[db_module.NEWS].insert_one(doc)
    except DuplicateKeyError:
        return False
    return True


def prune_external(database: Database, *, keep_days: int = 45) -> int:
    """Delete machine-ingested articles older than ``keep_days`` to bound growth."""
    cutoff = utcnow() - timedelta(days=keep_days)
    result = database[db_module.NEWS].delete_many(
        {"external": True, "published_at": {"$lt": cutoff}}
    )
    return result.deleted_count
