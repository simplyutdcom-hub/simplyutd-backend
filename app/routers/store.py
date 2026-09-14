"""Public store endpoints (products, categories, testimonials)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pymongo import DESCENDING
from pymongo.database import Database

from .. import db as db_module
from ..db import get_db
from ..schemas import ListResponse
from ..services.seed import STORE_CATEGORIES, TESTIMONIALS
from ..utils import serialize_many

router = APIRouter(prefix="/api/store", tags=["store"])


def _store_meta(database: Database) -> dict:
    doc = database[db_module.META].find_one({"key": "store_meta"}, {"_id": 0}) or {}
    return {
        "categories": doc.get("categories", STORE_CATEGORIES),
        "testimonials": doc.get("testimonials", TESTIMONIALS),
    }


@router.get("/categories")
def categories() -> dict:
    return {"categories": STORE_CATEGORIES}


@router.get("/testimonials")
def testimonials(database: Database = Depends(get_db)) -> dict:
    return {"items": _store_meta(database)["testimonials"]}


@router.get("/featured")
def featured(database: Database = Depends(get_db)) -> dict:
    doc = database[db_module.PRODUCTS].find_one(
        {"status": "Published", "featured": True}, {"_id": 0}
    )
    if doc is None:
        doc = database[db_module.PRODUCTS].find_one({"status": "Published"}, {"_id": 0})
    return {"product": doc}


@router.get("/products", response_model=ListResponse)
def list_products(
    category: str | None = None,
    q: str | None = None,
    limit: int = Query(50, ge=1, le=100),
    skip: int = Query(0, ge=0),
    database: Database = Depends(get_db),
) -> ListResponse:
    query: dict = {"status": "Published"}
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
    items = serialize_many(list(cursor))
    total = database[db_module.PRODUCTS].count_documents(query)
    return ListResponse(items=items, total=total, limit=limit, skip=skip)


@router.get("/products/{product_id}")
def get_product(product_id: str, database: Database = Depends(get_db)) -> dict:
    doc = database[db_module.PRODUCTS].find_one(
        {"$or": [{"id": product_id}, {"slug": product_id}]}, {"_id": 0}
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    return doc


@router.post("/products/{product_id}/click")
def register_click(product_id: str, database: Database = Depends(get_db)) -> dict:
    result = database[db_module.PRODUCTS].update_one(
        {"$or": [{"id": product_id}, {"slug": product_id}]}, {"$inc": {"clicks": 1}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Product not found.")
    return {"ok": True}
