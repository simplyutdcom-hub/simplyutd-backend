"""Admin hub-content management (fixtures, results, table, squad, overview)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pymongo.database import Database

from ... import db as db_module
from ...db import get_db
from ...schemas import HubSectionUpsert
from ...utils import utcnow

router = APIRouter(prefix="/hub", tags=["admin:hub"])

_SECTIONS = {"overview", "fixtures", "results", "standings", "compare", "squad", "position_order", "team_logos"}


@router.get("")
def list_sections(database: Database = Depends(get_db)) -> dict:
    docs = database[db_module.HUB].find({}, {"_id": 0}).sort("key", 1)
    return {doc["key"]: doc.get("data") for doc in docs}


@router.put("/{section}")
def upsert_section(
    section: str, payload: HubSectionUpsert, database: Database = Depends(get_db)
) -> dict:
    if section not in _SECTIONS:
        raise HTTPException(status_code=404, detail=f"Unknown hub section '{section}'.")
    # ``override`` marks a hand-crafted section so the public router prefers it
    # over the live RSS/Wikipedia derivation.
    database[db_module.HUB].update_one(
        {"key": section},
        {
            "$set": {
                "key": section,
                "data": payload.data,
                "override": True,
                "updated_at": utcnow(),
            }
        },
        upsert=True,
    )
    return {"ok": True, "section": section}
