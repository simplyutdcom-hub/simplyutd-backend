"""Admin news management + RSS ingestion controls."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pymongo.database import Database

from ...db import get_db
from ...schemas import DeleteResponse, ListResponse, NewsCreate, NewsUpdate
from ...services import news_service
from ...services.ingest import last_ingest, run_ingest

router = APIRouter(prefix="/news", tags=["admin:news"])


@router.get("", response_model=ListResponse)
def list_news(
    status: str | None = "all",
    category: str | None = None,
    featured: bool | None = None,
    q: str | None = None,
    limit: int = Query(50, ge=1, le=100),
    skip: int = Query(0, ge=0),
    database: Database = Depends(get_db),
) -> ListResponse:
    items, total = news_service.list_news(
        database,
        status=status,
        category=category,
        featured=featured,
        q=q,
        limit=limit,
        skip=skip,
        # Admin listings show the whole corpus in plain chronological order so
        # newly created stories are always visible, unlike the ranked public feed.
        united_only=False,
    )
    return ListResponse(items=items, total=total, limit=limit, skip=skip)


@router.post("", status_code=201)
def create(payload: NewsCreate, database: Database = Depends(get_db)) -> dict:
    return news_service.create_news(database, payload)


@router.get("/ingest/status")
def ingest_status(database: Database = Depends(get_db)) -> dict:
    return {"last": last_ingest(database)}


@router.post("/ingest")
def trigger_ingest(database: Database = Depends(get_db)) -> dict:
    """Manually trigger an RSS pull (runs inline so the caller gets a summary)."""
    return run_ingest(database)


@router.get("/{news_id}")
def get_news(news_id: str, database: Database = Depends(get_db)) -> dict:
    doc = news_service.get_news(database, news_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Article not found.")
    return doc


@router.put("/{news_id}")
def update_news(news_id: str, payload: NewsUpdate, database: Database = Depends(get_db)) -> dict:
    doc = news_service.update_news(database, news_id, payload)
    if doc is None:
        raise HTTPException(status_code=404, detail="Article not found.")
    return doc


@router.delete("/{news_id}", response_model=DeleteResponse)
def delete_news(news_id: str, database: Database = Depends(get_db)) -> DeleteResponse:
    if not news_service.delete_news(database, news_id):
        raise HTTPException(status_code=404, detail="Article not found.")
    return DeleteResponse(id=news_id)
