"""Admin CRUD for the RSS feeds the ingestion job pulls from."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from ...db import get_db
from ...schemas import DeleteResponse, ListResponse, NewsSourceCreate, NewsSourceUpdate
from ...services import news_sources

router = APIRouter(prefix="/news/sources", tags=["admin:news"])


@router.get("", response_model=ListResponse)
def list_sources(database: Database = Depends(get_db)) -> ListResponse:
    items = news_sources.list_sources(database)
    return ListResponse(items=items, total=len(items))


@router.post("", status_code=201)
def create_source(payload: NewsSourceCreate, database: Database = Depends(get_db)) -> dict:
    try:
        return news_sources.create_source(database, payload)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="That feed URL is already configured.") from None


@router.post("/restore-defaults")
def restore_defaults(database: Database = Depends(get_db)) -> dict:
    """Re-add any of the built-in feeds that were removed."""
    added = news_sources.restore_defaults(database)
    return {"added": added, "total": len(news_sources.list_sources(database))}


@router.get("/{source_id}")
def get_source(source_id: str, database: Database = Depends(get_db)) -> dict:
    doc = news_sources.get_source(database, source_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    return doc


@router.patch("/{source_id}")
def update_source(
    source_id: str, payload: NewsSourceUpdate, database: Database = Depends(get_db)
) -> dict:
    try:
        doc = news_sources.update_source(database, source_id, payload)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="That feed URL is already configured.") from None
    if doc is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    return doc


@router.delete("/{source_id}", response_model=DeleteResponse)
def delete_source(source_id: str, database: Database = Depends(get_db)) -> DeleteResponse:
    existing = news_sources.get_source(database, source_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    if existing.get("builtin"):
        # Built-ins can be switched off (or renamed/repointed) but not removed,
        # so a fresh deploy always ships with a working ingestion list.
        raise HTTPException(
            status_code=409, detail="Built-in sources can be switched off instead of removed."
        )
    news_sources.delete_source(database, source_id)
    return DeleteResponse(id=source_id)
