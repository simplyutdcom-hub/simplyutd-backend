"""Public news endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pymongo.database import Database

from ..db import get_db
from ..schemas import ListResponse
from ..services import news_service

router = APIRouter(prefix="/api/news", tags=["news"])


@router.get("", response_model=ListResponse)
def list_news(
    category: str | None = None,
    source: str | None = None,
    featured: bool | None = None,
    q: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    skip: int = Query(0, ge=0),
    min_items: int = Query(8, ge=1, le=50),
    database: Database = Depends(get_db),
) -> ListResponse:
    items, total = news_service.list_news(
        database,
        status="Published",
        category=category,
        source=source,
        featured=featured,
        q=q,
        limit=limit,
        skip=skip,
        min_items=min_items,
    )
    return ListResponse(items=items, total=total, limit=limit, skip=skip)


@router.get("/ticker")
def ticker(limit: int = Query(6, ge=1, le=20), database: Database = Depends(get_db)) -> dict:
    return {"items": news_service.ticker(database, limit)}


@router.get("/categories")
def categories(database: Database = Depends(get_db)) -> dict:
    """Section names and how many club-relevant stories each holds."""
    return news_service.category_counts(database)


@router.get("/{news_id}")
def get_news(
    news_id: str,
    related: int = Query(4, ge=0, le=12),
    database: Database = Depends(get_db),
) -> dict:
    """Full article for the story detail page: body text plus related stories."""
    doc = news_service.get_news(database, news_id)
    if doc is None or doc.get("status") != "Published":
        raise HTTPException(status_code=404, detail="Article not found.")
    return news_service.detail(database, doc, limit=related)


@router.post("/{news_id}/view")
def register_view(news_id: str, database: Database = Depends(get_db)) -> dict:
    doc = news_service.increment_views(database, news_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Article not found.")
    return {"ok": True, "views": doc.get("views", 0)}
