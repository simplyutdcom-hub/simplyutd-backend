"""Admin dashboard statistics, chart series and recent activity."""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from pymongo import DESCENDING
from pymongo.database import Database

from ... import db as db_module
from ...db import get_db
from ...utils import utcnow

router = APIRouter(prefix="/stats", tags=["admin:stats"])


def _product_clicks(database: Database) -> int:
    total = 0
    for doc in database[db_module.PRODUCTS].find({}, {"clicks": 1}):
        total += int(doc.get("clicks") or 0)
    return total


def _active_users(database: Database, window_days: int = 7) -> int:
    cutoff = utcnow() - timedelta(days=window_days)
    paths = database[db_module.EVENTS].distinct("path", {"created_at": {"$gte": cutoff}})
    return len([p for p in paths if p])


@router.get("/overview")
def overview(database: Database = Depends(get_db)) -> dict:
    unread = database[db_module.MESSAGES].count_documents({"unread": True})
    total_users = database[db_module.SUBSCRIBERS].estimated_document_count()
    stats = [
        {"label": "Active Users", "value": _active_users(database), "change": "", "positive": True},
        {"label": "Total Users", "value": total_users, "change": "", "positive": True},
        {"label": "Product Clicks", "value": _product_clicks(database), "change": "", "positive": True},
        {"label": "Unread Messages", "value": unread, "change": "", "positive": False},
    ]
    return {
        "stats": stats,
        "counts": {
            "news": database[db_module.NEWS].estimated_document_count(),
            "products": database[db_module.PRODUCTS].estimated_document_count(),
            "messages": database[db_module.MESSAGES].estimated_document_count(),
            "subscribers": total_users,
            "unread": unread,
        },
    }


@router.get("/chart")
def chart(days: int = Query(7, ge=1, le=30), database: Database = Depends(get_db)) -> dict:
    cutoff = (utcnow() - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    buckets: dict[str, dict[str, int]] = defaultdict(
        lambda: {"pageViews": 0, "visitors": 0, "productClicks": 0}
    )
    seen_visitors: set[str] = set()

    events = database[db_module.EVENTS].find(
        {"created_at": {"$gte": cutoff}}, {"type": 1, "path": 1, "created_at": 1}
    )
    for event in events:
        created = event.get("created_at")
        key = created.strftime("%b %d") if created else "—"
        etype = (event.get("type") or "").lower()
        if etype in {"page_view", "pageview", "view"}:
            buckets[key]["pageViews"] += 1
        elif etype in {"visitor", "session"}:
            buckets[key]["visitors"] += 1
        elif etype in {"product_click", "click"}:
            buckets[key]["productClicks"] += 1
        visitor = event.get("path")
        if visitor:
            seen_visitors.add(f"{key}:{visitor}")

    # Fill missing days so the chart is continuous.
    labels = [
        (cutoff + timedelta(days=i)).strftime("%b %d") for i in range((utcnow().date() - cutoff.date()).days + 1)
    ]
    series = []
    for label in labels:
        bucket = buckets.get(label, {"pageViews": 0, "visitors": 0, "productClicks": 0})
        series.append({"day": label, **bucket})

    return {
        "legend": [
            {"label": "Page Views", "color": "#DA291C"},
            {"label": "Visitors", "color": "#FAFAFA"},
            {"label": "Product Clicks", "color": "#A4A4AB"},
        ],
        "series": series,
        "uniqueVisitors": len(seen_visitors),
    }


@router.get("/activity")
def activity(database: Database = Depends(get_db), limit: int = Query(10, ge=1, le=50)) -> dict:
    events = list(
        database[db_module.EVENTS]
        .find({}, {"_id": 0})
        .sort("created_at", DESCENDING)
        .limit(limit)
    )
    messages = list(
        database[db_module.MESSAGES]
        .find({}, {"_id": 0})
        .sort("created_at", DESCENDING)
        .limit(limit)
    )
    return {"events": events, "messages": messages}
