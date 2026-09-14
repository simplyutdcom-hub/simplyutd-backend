"""Public Hub endpoints (stats, fixtures, results, table, squad, comparison).

The payload is derived live from the ingested RSS corpus and the club's
Wikipedia season records (see :mod:`app.services.hub_service`). Admin edits made
through the admin Hub API override the derived value for that section.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pymongo.database import Database

from ..db import get_db
from ..services import hub_service

router = APIRouter(prefix="/api/hub", tags=["hub"])

_SECTIONS = {
    "hero",
    "overview",
    "fixtures",
    "results",
    "standings",
    "compare",
    "squad",
}

# Provenance of the payload rather than a panel of its own, so it is only served
# as part of the combined response.
_META_KEYS = ("standings_meta",)


@router.get("")
def hub_all(database: Database = Depends(get_db)) -> dict:
    """Return every hub section in a single payload for first paint."""
    built = hub_service.build_hub(database)
    payload = {key: built.get(key) for key in sorted(_SECTIONS)}
    payload.update({key: built.get(key) for key in _META_KEYS})
    return payload


@router.get("/{section}")
def hub_section(section: str, database: Database = Depends(get_db)) -> Any:
    if section not in _SECTIONS:
        raise HTTPException(status_code=404, detail=f"Unknown hub section '{section}'.")
    data = hub_service.section(database, section)
    if data is None:
        raise HTTPException(status_code=404, detail="Section not found.")
    return data
