"""Live United endpoints.

"Live United" is the rolling stream of the newest Manchester United updates that
the home page shows under the hero story. Each entry links back to its story page
for the full read, and has its own lightweight detail view here so the feed can be
followed without leaving the live list.

The stream is ordered by the United-first ranking (see
:mod:`app.services.feed_rank`) so the newest *relevant* update leads, and it is
never led by a story about another club.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pymongo.database import Database

from ..db import get_db
from ..services import news_service

router = APIRouter(prefix="/api/live", tags=["live"])


@router.get("")
def list_live(
    limit: int = Query(20, ge=1, le=50),
    skip: int = Query(0, ge=0),
    database: Database = Depends(get_db),
) -> dict:
    """The live update stream, most relevant/recent first."""
    items, total = news_service.list_news(
        database,
        status="Published",
        limit=limit,
        skip=skip,
    )
    return {
        "items": [news_service.live_item(doc) for doc in items],
        "total": total,
        "limit": limit,
        "skip": skip,
    }


@router.get("/{live_id}")
def get_live(
    live_id: str,
    related: int = Query(4, ge=0, le=12),
    database: Database = Depends(get_db),
) -> dict:
    """A single live update plus the other recent updates around it."""
    doc = news_service.get_news(database, live_id)
    if doc is None or doc.get("status") != "Published":
        raise HTTPException(status_code=404, detail="Update not found.")
    payload = news_service.detail(database, doc, limit=related)
    payload["live"] = news_service.related(database, doc, limit=related, shape="live")
    return payload
