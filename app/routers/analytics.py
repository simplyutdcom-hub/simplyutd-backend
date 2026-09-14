"""Public analytics event capture."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pymongo.database import Database

from .. import db as db_module
from ..db import get_db
from ..schemas import AnalyticsEvent, AnalyticsResponse
from ..utils import new_id, utcnow

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.post("/event", response_model=AnalyticsResponse, status_code=202)
def track(event: AnalyticsEvent, database: Database = Depends(get_db)) -> AnalyticsResponse:
    database[db_module.EVENTS].insert_one(
        {
            "id": new_id(),
            "type": event.type,
            "path": event.path,
            "label": event.label,
            "meta": event.meta,
            "created_at": utcnow(),
        }
    )
    return AnalyticsResponse()
