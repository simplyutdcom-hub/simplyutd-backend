"""Health, diagnostics and service-status endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pymongo.database import Database

from .. import __version__, db as db_module
from ..config import settings
from ..db import get_db
from ..services import cloudinary_service, mailer
from ..services.ingest import last_ingest

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health() -> dict:
    """Liveness probe used by the frontend and hosting platform."""
    return {"status": "ok", "app": settings.app_name, "version": __version__}


@router.get("/diag")
def diagnostics(database: Database = Depends(get_db)) -> dict:
    """Report which integrations are configured and whether the DB is reachable."""
    mongo_ok = True
    mongo_error: str | None = None
    try:
        database.command("ping")
    except Exception as exc:  # noqa: BLE001
        mongo_ok = False
        mongo_error = str(exc)

    return {
        "app": settings.app_name,
        "version": __version__,
        "environment": settings.environment,
        "mongo": {
            "connected": mongo_ok,
            "database": settings.mongo_db,
            "error": mongo_error,
        },
        "cloudinary": {
            "configured": cloudinary_service.is_configured(),
            "folder": settings.cloudinary_folder,
        },
        "email": {
            "configured": mailer.is_configured(),
            "from": settings.resend_from,
            "notify_to": bool(settings.notify_to),
        },
        "admin": {"enabled": bool(settings.admin_api_key)},
        "rss": {
            "enabled": settings.ingest_enabled,
            "interval_minutes": settings.ingest_interval_minutes,
            "feed_count": len(settings.rss_feeds),
            "last_ingest": last_ingest(database),
        },
        "collections": {
            "news": _safe_count(database, db_module.NEWS),
            "products": _safe_count(database, db_module.PRODUCTS),
            "messages": _safe_count(database, db_module.MESSAGES),
            "subscribers": _safe_count(database, db_module.SUBSCRIBERS),
        },
    }


def _safe_count(database: Database, collection: str) -> int | None:
    try:
        return database[collection].estimated_document_count()
    except Exception:  # noqa: BLE001
        return None
