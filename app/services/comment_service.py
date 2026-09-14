"""Threaded comments for stories and live updates (YouTube-style).

Comments are user generated content stored in the ``comments`` collection and
keyed by ``(target_type, target_id)``. Stories and per-item live updates are
backed by a news document, so their ``target_id`` is a news id and the
denormalised ``news.comments`` counter is kept in sync after every mutation.
Matchday live-chat rooms (``matchday-YYYY-MM-DD``) are virtual: they have no
news document, so they are exempt from that existence check.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any

from pymongo import ASCENDING, DESCENDING
from pymongo.database import Database

from .. import db as db_module
from ..utils import initials_from, new_id, serialize, strip_html, utcnow

logger = logging.getLogger("simplyutd.comments")

TARGETS = ("story", "live")
MAX_BODY = 2000
MAX_AUTHOR = 60
DEFAULT_AUTHOR = "SimplyUtd fan"
MAX_LIMIT = 500

SORT_SPECS: dict[str, list[tuple[str, int]]] = {
    "newest": [("created_at", DESCENDING)],
    "oldest": [("created_at", ASCENDING)],
    "top": [("likes", DESCENDING), ("created_at", DESCENDING)],
}

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
LIVE_ROOM_RE = re.compile(r"^matchday-\d{4}-\d{2}-\d{2}$")


def _minutes(count: int) -> timedelta:
    return timedelta(minutes=count)


class CommentError(Exception):
    """Raised for invalid comment operations; carries an HTTP status."""

    def __init__(self, detail: str, status: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status = status


def _scope_by_type(target_type: str) -> dict[str, Any]:
    return {"target_type": target_type}


def normalise_target(target_type: str) -> str:
    value = (target_type or "").strip().lower()
    if value not in TARGETS:
        raise CommentError(f"Unsupported target_type '{target_type}'", status=400)
    return value


def clean_body(raw: str) -> str:
    """Strip HTML/control characters and collapse whitespace."""
    text = strip_html(raw)
    text = _CONTROL_RE.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        raise CommentError("Comment cannot be empty", status=422)
    if len(text) > MAX_BODY:
        raise CommentError(f"Comment must be {MAX_BODY} characters or fewer", status=422)
    return text


def clean_author(raw: str | None) -> str:
    name = strip_html(raw or "").strip()
    if not name:
        return DEFAULT_AUTHOR
    return name[:MAX_AUTHOR]


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _public(doc: dict[str, Any], viewer_id: str | None = None) -> dict[str, Any]:
    """JSON-safe view of a comment, shared by REST and the WebSocket.

    ``visitor_id`` stays server-side (it is a persistent browser handle); the
    caller only learns whether the comment belongs to them, via ``mine``.
    """
    out = serialize(doc) or {}
    out.setdefault("likes", 0)
    out["likes"] = int(out.get("likes") or 0)
    out["replies"] = []
    owner = out.pop("visitor_id", None)
    out["mine"] = bool(viewer_id) and bool(owner) and owner == viewer_id
    voters = [v for v in (out.pop("liked_by", None) or []) if v]
    out["liked"] = bool(viewer_id) and viewer_id in voters
    for key in ("created_at", "updated_at", "published_at"):
        if key in out:
            out[key] = _iso(out[key])
    return out


def _scope(target_id: str) -> dict[str, Any]:
    """A thread belongs to an article, so stories and live share one thread."""
    return {"target_id": target_id}


def _require_article(database: Database, target_id: str) -> dict[str, Any]:
    doc = database[db_module.NEWS].find_one({"id": target_id})
    if doc is None:
        raise CommentError("That article no longer exists", status=404)
    return doc


def is_live_room(target_id: str) -> bool:
    """True for the virtual matchday chat rooms shared by all fans."""
    return bool(LIVE_ROOM_RE.match((target_id or "").strip()))


def _require_target(database: Database, target_type: str, target_id: str) -> None:
    """Stories and news-backed live items must exist; chat rooms are virtual."""
    if target_type == "live" and is_live_room(target_id):
        return
    _require_article(database, target_id)


def viewer_state(database: Database, target_id: str, visitor_id: str | None) -> dict[str, list[str]]:
    """Ids the viewer authored / liked in this thread — drives ``mine`` and the
    filled like pill in the UI, without ever exposing another visitor's id."""
    viewer = (visitor_id or "").strip()
    if not viewer:
        return {"mine": [], "liked": []}
    mine: list[str] = []
    liked: list[str] = []
    for doc in database[db_module.COMMENTS].find(
        _scope(target_id), {"id": 1, "visitor_id": 1, "liked_by": 1}
    ):
        if doc.get("visitor_id") == viewer:
            mine.append(doc["id"])
        if viewer in (doc.get("liked_by") or []):
            liked.append(doc["id"])
    return {"mine": mine, "liked": liked}


def count(database: Database, target_id: str) -> int:
    return int(database[db_module.COMMENTS].count_documents(_scope(target_id)))


def sync_count(database: Database, target_id: str) -> int:
    """Recompute ``news.comments`` for the target and return the new total."""
    total = count(database, target_id)
    database[db_module.NEWS].update_one({"id": target_id}, {"$set": {"comments": total}})
    return total


def list_thread(
    database: Database,
    target_type: str,
    target_id: str,
    *,
    sort: str = "newest",
    limit: int = MAX_LIMIT,
    viewer_id: str | None = None,
) -> dict[str, Any]:
    """Return top-level comments with their replies nested, newest first."""
    target_type = normalise_target(target_type)
    spec = SORT_SPECS.get(sort) or SORT_SPECS["newest"]
    limit = max(1, min(int(limit or MAX_LIMIT), MAX_LIMIT))
    cursor = database[db_module.COMMENTS].find(_scope(target_id)).sort(spec).limit(limit)
    docs = [_public(doc, viewer_id) for doc in cursor]

    by_id = {doc["id"]: doc for doc in docs}
    roots: list[dict[str, Any]] = []
    for doc in docs:
        parent_id = doc.get("parent_id")
        parent = by_id.get(parent_id) if parent_id else None
        if parent is not None:
            parent["replies"].append(doc)
        else:
            roots.append(doc)

    for root in roots:
        root["replies"].sort(key=lambda item: item.get("created_at") or utcnow())
    return {
        "items": roots,
        "total": count(database, target_id),
        "target_type": target_type,
        "target_id": target_id,
        "sort": sort,
    }


def create(
    database: Database,
    *,
    target_type: str,
    target_id: str,
    body: str,
    author: str | None = None,
    parent_id: str | None = None,
    visitor_id: str | None = None,
) -> tuple[dict[str, Any], int]:
    """Insert a comment (or reply) and return ``(comment, new_total)``."""
    target_type = normalise_target(target_type)
    _require_target(database, target_type, target_id)
    text = clean_body(body)
    parent: dict[str, Any] | None = None
    if parent_id:
        parent = database[db_module.COMMENTS].find_one({"id": parent_id})
        if parent is None:
            raise CommentError("The comment you replied to no longer exists", status=404)
        if parent.get("target_id") != target_id:
            raise CommentError("Reply target does not match this thread", status=400)
        # Keep threads two levels deep: replies to replies attach to the root.
        if parent.get("parent_id"):
            parent_id = parent["parent_id"]

    now = utcnow()
    who = clean_author(author)
    doc = {
        "id": new_id(),
        "target_type": target_type,
        "target_id": target_id,
        "parent_id": parent_id or None,
        "author": who,
        "initials": initials_from(who),
        "body": text,
        "likes": 0,
        "liked_by": [],
        "visitor_id": visitor_id,
        "created_at": now,
        "updated_at": now,
    }
    database[db_module.COMMENTS].insert_one(dict(doc))
    total = sync_count(database, target_id)
    logger.debug("Comment %s created on %s/%s", doc["id"], target_type, target_id)
    return _public(doc, visitor_id), total


def toggle_like(database: Database, comment_id: str, visitor_id: str) -> dict[str, Any]:
    """Toggle a like for ``visitor_id``; returns the updated public comment."""
    voter = (visitor_id or "").strip()
    if not voter:
        raise CommentError("A visitor id is required to like a comment", status=422)
    doc = database[db_module.COMMENTS].find_one({"id": comment_id})
    if doc is None:
        raise CommentError("Comment not found", status=404)

    liked_by = list(doc.get("liked_by") or [])
    if voter in liked_by:
        liked_by.remove(voter)
        liked = False
    else:
        liked_by.append(voter)
        liked = True
    database[db_module.COMMENTS].update_one(
        {"id": comment_id},
        {"$set": {"liked_by": liked_by, "likes": len(liked_by), "updated_at": utcnow()}},
    )
    updated = database[db_module.COMMENTS].find_one({"id": comment_id}) or doc
    public = _public(updated, voter)
    public["liked"] = liked
    return public


def remove(
    database: Database,
    comment_id: str,
    *,
    visitor_id: str | None = None,
    is_admin: bool = False,
) -> tuple[str, int]:
    """Delete a comment and its replies. Returns ``(target_id, new_total)``."""
    doc = database[db_module.COMMENTS].find_one({"id": comment_id})
    if doc is None:
        raise CommentError("Comment not found", status=404)
    if not is_admin:
        if not visitor_id or doc.get("visitor_id") != visitor_id:
            raise CommentError("You can only delete your own comments", status=403)

    ids = [comment_id]
    for reply in database[db_module.COMMENTS].find({"parent_id": comment_id}, {"id": 1}):
        ids.append(reply["id"])
    database[db_module.COMMENTS].delete_many({"id": {"$in": ids}})

    target_id = doc.get("target_id") or ""
    total = sync_count(database, target_id) if target_id else 0
    return target_id, total


def stats(database: Database) -> dict[str, Any]:
    """Aggregate totals used by the admin dashboard."""
    pipeline = [
        {"$group": {"_id": "$target_type", "total": {"$sum": 1}, "likes": {"$sum": "$likes"}}},
        {"$sort": {"_id": ASCENDING}},
    ]
    by_target: dict[str, dict[str, int]] = {}
    total = 0
    likes = 0
    try:
        for row in database[db_module.COMMENTS].aggregate(pipeline):
            key = row.get("_id") or "unknown"
            by_target[key] = {"total": int(row.get("total") or 0), "likes": int(row.get("likes") or 0)}
            total += int(row.get("total") or 0)
            likes += int(row.get("likes") or 0)
    except Exception:  # noqa: BLE001 - mongomock aggregation quirks must not 500
        logger.debug("Falling back to per-target counts", exc_info=True)
        for key in TARGETS:
            found = int(database[db_module.COMMENTS].count_documents(_scope_by_type(key)))
            by_target[key] = {"total": found, "likes": 0}
            total += found

    recent = [
        _public(doc)
        for doc in database[db_module.COMMENTS]
        .find()
        .sort([("created_at", DESCENDING)])
        .limit(10)
    ]
    return {"total": total, "likes": likes, "by_target": by_target, "recent": recent}


def recent_for_articles(
    database: Database, target_type: str, article_ids: list[str], per_article: int = 1
) -> dict[str, list[dict[str, Any]]]:
    """Latest comments per article id — used to show text on live cards."""
    if not article_ids:
        return {}
    out: dict[str, list[dict[str, Any]]] = {article_id: [] for article_id in article_ids}
    cursor = (
        database[db_module.COMMENTS]
        .find({"target_id": {"$in": list(article_ids)}})
        .sort([("created_at", DESCENDING)])
        .limit(per_article * max(1, min(len(article_ids), 50)))
    )
    for doc in cursor:
        bucket = out.setdefault(doc.get("target_id"), [])
        if len(bucket) < per_article:
            bucket.append(_public(doc))
    return out


# --- Demo content ----------------------------------------------------------
DEMO_AUTHORS: tuple[str, ...] = (
    "RedsSince92",
    "StretfordEnd",
    "MUTID_Mae",
    "OldTraffordRoar",
    "BusbyBabe",
    "CantonaKing7",
    "GlazerOutKerry",
    "OTFaithful",
)

DEMO_BODIES: tuple[str, ...] = (
    "Big result that. The press in the second half was exactly what we've been asking for.",
    "Ten Hag finally got the midfield balance right — the double pivot worked a treat.",
    "That finish was outrageous, watch it again on the replay.",
    "Still not convinced defensively, we sit too deep once we lead.",
    "Best atmosphere at Old Trafford in ages, the Stretford End was bouncing.",
    "Injury list is the real story here, we cannot keep going like this.",
    "Give Garnacho the full 90 every week and he'll get 15 goals this season.",
    "Anyone else notice how much better we look without the ball since the change of shape?",
    "Referee had a shocker but we should have put it to bed long before that.",
    "Academy lads getting minutes is the best part of this season for me.",
    "Three points and a clean sheet, can't ask for more on a Tuesday night.",
    "That counter attack was pure United, end to end in about eight seconds.",
)

DEMO_REPLIES: tuple[str, ...] = (
    "Agree with this 100%.",
    "Spot on, said the same to my mate at half time.",
    "Disagree — the shape was fine, it was the finishing that let us down.",
    "Reckon we'll see the same XI at the weekend?",
    "This aged well within ten minutes of posting, ha.",
)


def seed_demo_comments(
    database: Database, *, articles: int = 6, per_article: int = 3, replies: int = 2
) -> int:
    """Attach a few starter comments to recent articles that have none.

    Used by the startup seed and after each ingest cycle so freshly fetched
    stories are not empty shells. Content is original fan chatter owned by this
    app (never scraped), and articles that already have comments are skipped.
    """
    seed_tag = "seed"
    if database[db_module.COMMENTS].count_documents({"origin": seed_tag}) >= articles * per_article:
        return 0

    cursor = (
        database[db_module.NEWS]
        .find({"status": "Published"})
        .sort([("published_at", DESCENDING)])
        .limit(60)
    )
    created = 0
    touched = 0
    for doc in cursor:
        if touched >= articles:
            break
        target_id = doc.get("id")
        if not target_id:
            continue
        if int(doc.get("comments") or 0) > 0 or count(database, target_id) > 0:
            continue
        touched += 1
        offset = sum(target_id.encode("utf-8")) % len(DEMO_BODIES)
        base = utcnow() - _minutes(7 + (offset % 5) * 13)
        roots: list[str] = []
        for index in range(per_article):
            author = DEMO_AUTHORS[(offset + index) % len(DEMO_AUTHORS)]
            comment, _ = create(
                database,
                target_type="story",
                target_id=target_id,
                body=DEMO_BODIES[(offset + index) % len(DEMO_BODIES)],
                author=author,
                visitor_id=f"seed-{author}",
            )
            likes = (offset + index * 3) % 17
            database[db_module.COMMENTS].update_one(
                {"id": comment["id"]},
                {
                    "$set": {
                        "created_at": base + _minutes(index * 9),
                        "updated_at": base + _minutes(index * 9),
                        "likes": likes,
                        "liked_by": [f"fan-{n}" for n in range(likes)],
                        "origin": seed_tag,
                    }
                },
            )
            roots.append(comment["id"])
            created += 1

        for index in range(replies):
            author = DEMO_AUTHORS[(offset + index + 3) % len(DEMO_AUTHORS)]
            _, _ = create(
                database,
                target_type="story",
                target_id=target_id,
                body=DEMO_REPLIES[(offset + index) % len(DEMO_REPLIES)],
                author=author,
                parent_id=roots[index % len(roots)],
                visitor_id=f"seed-{author}",
            )
            database[db_module.COMMENTS].update_one(
                {"parent_id": roots[index % len(roots)], "author": author, "target_id": target_id},
                {"$set": {"created_at": base + _minutes(30 * (index + 1)), "origin": seed_tag}},
            )
            created += 1

        sync_count(database, target_id)

    if created:
        logger.info("Seeded %d demo comments across %d articles", created, touched)
    return created
