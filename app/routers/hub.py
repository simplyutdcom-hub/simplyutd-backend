"""Public Hub endpoints (stats, fixtures, results, table, squad, comparison).

The payload is derived live from the ingested RSS corpus and the club's
Wikipedia season records (see :mod:`app.services.hub_service`). Admin edits made
through the admin Hub API override the derived value for that section.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from pymongo.database import Database

from ..db import get_db
from ..services import hub_service, live_score_service

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
_META_KEYS = ("standings_meta", "schedule_meta")


@router.get("")
def hub_all(
    response: Response, background: BackgroundTasks, database: Database = Depends(get_db)
) -> dict:
    """Return every hub section in a single payload for first paint.

    The payload is answered from the cached season data, and a refresh is
    started after it goes out when that data has gone stale, so the fixtures and
    results a visitor sees are never more than a poll or a visit out of date.
    """
    built = hub_service.build_hub(database)
    payload = {key: built.get(key) for key in sorted(_SECTIONS)}
    payload.update({key: built.get(key) for key in _META_KEYS})
    background.add_task(hub_service.revalidate)
    # Freshness is decided by the caches above, so no intermediate copy of this
    # payload - browser or CDN - may outlive the request.
    response.headers["cache-control"] = "no-store"
    return payload


@router.get("/live-match")
def hub_live_match(response: Response) -> dict:
    """United's current match for the Live United score card, or nothing at all.

    Declared ahead of the ``/{section}`` catch-all so "live-match" is not read
    as a Hub section name.
    """
    # The card polls this every minute for a score or a kick-off countdown, so no
    # intermediate copy of it may be served in between.
    response.headers["cache-control"] = "no-store"
    return live_score_service.united_match()


@router.get("/{section}")
def hub_section(
    section: str,
    response: Response,
    background: BackgroundTasks,
    database: Database = Depends(get_db),
) -> Any:
    if section not in _SECTIONS:
        raise HTTPException(status_code=404, detail=f"Unknown hub section '{section}'.")
    data = hub_service.section(database, section)
    if data is None:
        raise HTTPException(status_code=404, detail="Section not found.")
    background.add_task(hub_service.revalidate)
    response.headers["cache-control"] = "no-store"
    return data
