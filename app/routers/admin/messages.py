"""Admin inbox — contact messages."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pymongo import DESCENDING, ReturnDocument
from pymongo.database import Database

from ... import db as db_module
from ...db import get_db
from ...schemas import DeleteResponse, ListResponse, MessageReply, MessageUpdate
from ...utils import new_id, utcnow

router = APIRouter(prefix="/messages", tags=["admin:messages"])


@router.get("", response_model=ListResponse)
def list_messages(
    unread: bool | None = None,
    favourite: bool | None = None,
    q: str | None = None,
    limit: int = Query(100, ge=1, le=200),
    skip: int = Query(0, ge=0),
    database: Database = Depends(get_db),
) -> ListResponse:
    query: dict = {}
    if unread is not None:
        query["unread"] = unread
    if favourite is not None:
        query["favourite"] = favourite
    if q:
        query["$or"] = [
            {"name": {"$regex": q, "$options": "i"}},
            {"email": {"$regex": q, "$options": "i"}},
            {"subject": {"$regex": q, "$options": "i"}},
            {"message": {"$regex": q, "$options": "i"}},
        ]
    cursor = (
        database[db_module.MESSAGES]
        .find(query, {"_id": 0})
        .sort("created_at", DESCENDING)
        .skip(skip)
        .limit(limit)
    )
    items = list(cursor)
    total = database[db_module.MESSAGES].count_documents(query)
    return ListResponse(items=items, total=total, limit=limit, skip=skip)


@router.get("/stats")
def message_stats(database: Database = Depends(get_db)) -> dict:
    return {
        "total": database[db_module.MESSAGES].estimated_document_count(),
        "unread": database[db_module.MESSAGES].count_documents({"unread": True}),
        "favourites": database[db_module.MESSAGES].count_documents({"favourite": True}),
    }


@router.get("/{message_id}")
def get_message(message_id: str, database: Database = Depends(get_db)) -> dict:
    doc = database[db_module.MESSAGES].find_one({"id": message_id}, {"_id": 0})
    if doc is None:
        raise HTTPException(status_code=404, detail="Message not found.")
    return doc


@router.patch("/{message_id}")
def update_message(
    message_id: str, payload: MessageUpdate, database: Database = Depends(get_db)
) -> dict:
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        doc = database[db_module.MESSAGES].find_one({"id": message_id}, {"_id": 0})
        if doc is None:
            raise HTTPException(status_code=404, detail="Message not found.")
        return doc
    doc = database[db_module.MESSAGES].find_one_and_update(
        {"id": message_id},
        {"$set": updates},
        return_document=ReturnDocument.AFTER,
        projection={"_id": 0},
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="Message not found.")
    return doc


@router.post("/{message_id}/reply")
def reply_to_message(
    message_id: str, payload: MessageReply, database: Database = Depends(get_db)
) -> dict:
    """Append an admin reply to a message and mark it read."""
    reply = {"id": new_id(), "body": payload.body, "created_at": utcnow()}
    doc = database[db_module.MESSAGES].find_one_and_update(
        {"id": message_id},
        {"$push": {"replies": reply}, "$set": {"unread": False}},
        return_document=ReturnDocument.AFTER,
        projection={"_id": 0},
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="Message not found.")
    return doc


@router.delete("/{message_id}", response_model=DeleteResponse)
def delete_message(message_id: str, database: Database = Depends(get_db)) -> DeleteResponse:
    result = database[db_module.MESSAGES].delete_one({"id": message_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Message not found.")
    return DeleteResponse(id=message_id)
