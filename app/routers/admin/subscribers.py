"""Admin newsletter subscriber management."""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pymongo import DESCENDING
from pymongo.database import Database

from ... import db as db_module
from ...db import get_db
from ...schemas import DeleteResponse, ListResponse

router = APIRouter(prefix="/subscribers", tags=["admin:subscribers"])


@router.get("", response_model=ListResponse)
def list_subscribers(
    q: str | None = None,
    limit: int = Query(200, ge=1, le=500),
    skip: int = Query(0, ge=0),
    database: Database = Depends(get_db),
) -> ListResponse:
    query: dict = {}
    if q:
        query["email"] = {"$regex": q, "$options": "i"}
    cursor = (
        database[db_module.SUBSCRIBERS]
        .find(query, {"_id": 0})
        .sort("created_at", DESCENDING)
        .skip(skip)
        .limit(limit)
    )
    items = list(cursor)
    total = database[db_module.SUBSCRIBERS].count_documents(query)
    return ListResponse(items=items, total=total, limit=limit, skip=skip)


@router.get("/export")
def export_subscribers(database: Database = Depends(get_db)) -> StreamingResponse:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["email", "source", "created_at"])
    for doc in database[db_module.SUBSCRIBERS].find({}, {"_id": 0}).sort("created_at", DESCENDING):
        writer.writerow([doc.get("email"), doc.get("source"), doc.get("created_at")])
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=simplyutd-subscribers.csv"},
    )


@router.delete("/{subscriber_id}", response_model=DeleteResponse)
def delete_subscriber(subscriber_id: str, database: Database = Depends(get_db)) -> DeleteResponse:
    result = database[db_module.SUBSCRIBERS].delete_one(
        {"$or": [{"id": subscriber_id}, {"email": subscriber_id}]}
    )
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Subscriber not found.")
    return DeleteResponse(id=subscriber_id)
