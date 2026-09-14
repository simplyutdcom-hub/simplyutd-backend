"""Aggregated home-page feed.

The SPA's home screen needs four slices of the news feed (breaking ticker, live
updates, latest stories, trending). This endpoint assembles them from the
``news`` collection in one round-trip.

Every slice comes from the United-first ranking in
:mod:`app.services.feed_rank` rather than a plain date sort, so the front page is
driven by relevance to the club, freshness and engagement — and by nothing that
is actually about another team.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pymongo.database import Database

from .. import db as db_module
from ..db import get_db
from ..services import news_service
from ..utils import serialize_many

router = APIRouter(prefix="/api/home", tags=["home"])


@router.get("")
def home(database: Database = Depends(get_db)) -> dict:
    # One ranked pool feeds three of the four slices: relevance first, so a
    # three-hour-old United transfer line beats a fresh piece about a rival.
    pool = news_service.ranked(database, limit=40)

    ticker = [{"id": n["id"], "title": n["title"]} for n in pool[:6]]
    live = [news_service.live_item(n) for n in pool[:5]]
    stories = [
        {
            "id": n["id"],
            "image": n.get("image"),
            "title": n["title"],
            "sponsor": n.get("source") or "SimplyUtd",
            "time": news_service.minutes_ago(n.get("published_at")),
            "category": n.get("category"),
        }
        for n in pool[:12]
    ]

    # "Trending" stays engagement-ordered — that is the point of the section —
    # but the club gate is still applied so it can never be led by another team.
    by_views = serialize_many(
        list(
            database[db_module.NEWS]
            .find({"status": "Published"}, {"_id": 0})
            .sort("views", -1)
            .limit(40)
        )
    )
    trending = [
        {
            "id": n["id"],
            "title": n["title"],
            "sponsor": n.get("source") or "SimplyUtd",
            "views": n.get("views", 0),
        }
        for n in news_service.united_only(by_views, min_items=5)[:5]
    ]

    return {"ticker": ticker, "live": live, "stories": stories, "trending": trending}

