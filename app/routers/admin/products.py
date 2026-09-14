"""Admin product management."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pymongo import DESCENDING, ReturnDocument
from pymongo.database import Database

from ... import db as db_module
from ...db import get_db
from ...schemas import DeleteResponse, ListResponse, ProductCreate, ProductUpdate
from ...utils import new_id, serialize, slugify, utcnow

router = APIRouter(prefix="/products", tags=["admin:products"])


@router.get("", response_model=ListResponse)
def list_products(
    category: str | None = None,
    status: str | None = "all",
    q: str | None = None,
    limit: int = Query(100, ge=1, le=100),
    skip: int = Query(0, ge=0),
    database: Database = Depends(get_db),
) -> ListResponse:
    query: dict = {}
    if status and status.lower() != "all":
        query["status"] = status
    if category and category.lower() not in {"all", ""}:
        query["category"] = category
    if q:
        query["name"] = {"$regex": q, "$options": "i"}
    cursor = (
        database[db_module.PRODUCTS]
        .find(query, {"_id": 0})
        .sort("created_at", DESCENDING)
        .skip(skip)
        .limit(limit)
    )
    items = [serialize(d) for d in cursor]
    total = database[db_module.PRODUCTS].count_documents(query)
    return ListResponse(items=items, total=total, limit=limit, skip=skip)


@router.post("", status_code=201)
def create_product(payload: ProductCreate, database: Database = Depends(get_db)) -> dict:
    now = utcnow()
    doc = {
        "id": new_id(),
        "slug": f"{slugify(payload.name)}-{new_id()[:6]}",
        "clicks": 0,
        "created_at": now,
        "updated_at": now,
        **payload.model_dump(),
    }
    database[db_module.PRODUCTS].insert_one(doc)
    return serialize(doc)


@router.get("/{product_id}")
def get_product(product_id: str, database: Database = Depends(get_db)) -> dict:
    doc = database[db_module.PRODUCTS].find_one(
        {"$or": [{"id": product_id}, {"slug": product_id}]}, {"_id": 0}
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    return doc


@router.put("/{product_id}")
def update_product(
    product_id: str, payload: ProductUpdate, database: Database = Depends(get_db)
) -> dict:
    updates = payload.model_dump(exclude_unset=True, exclude_none=True)
    updates["updated_at"] = utcnow()
    doc = database[db_module.PRODUCTS].find_one_and_update(
        {"$or": [{"id": product_id}, {"slug": product_id}]},
        {"$set": updates},
        return_document=ReturnDocument.AFTER,
        projection={"_id": 0},
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    return doc


@router.delete("/{product_id}", response_model=DeleteResponse)
def delete_product(product_id: str, database: Database = Depends(get_db)) -> DeleteResponse:
    result = database[db_module.PRODUCTS].delete_one(
        {"$or": [{"id": product_id}, {"slug": product_id}]}
    )
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Product not found.")
    return DeleteResponse(id=product_id)
