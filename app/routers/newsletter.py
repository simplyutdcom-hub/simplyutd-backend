"""Public newsletter subscription (ported from the legacy waitlist flow)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from .. import db as db_module
from ..db import get_db
from ..schemas import SubscribeCreate, SubscribeResponse
from ..services import mailer
from ..utils import new_id, utcnow

logger = logging.getLogger("simplyutd.newsletter")

router = APIRouter(prefix="/api/newsletter", tags=["newsletter"])


@router.post("/subscribe", response_model=SubscribeResponse, status_code=201)
def subscribe(payload: SubscribeCreate, database: Database = Depends(get_db)) -> SubscribeResponse:
    email = str(payload.email).strip().lower()
    existing = database[db_module.SUBSCRIBERS].find_one({"email": email}, {"_id": 1})
    if existing:
        return SubscribeResponse(
            message="You're already on the list.",
            email=email,
            already_subscribed=True,
        )

    doc = {
        "id": new_id(),
        "email": email,
        "source": payload.source or "website",
        "created_at": utcnow(),
    }
    try:
        database[db_module.SUBSCRIBERS].insert_one(doc)
    except DuplicateKeyError:
        return SubscribeResponse(
            message="You're already on the list.",
            email=email,
            already_subscribed=True,
        )

    try:
        mailer.send_welcome_email(email)
        mailer.send_signup_notification(email)
    except Exception:  # noqa: BLE001
        logger.exception("Newsletter email dispatch failed")

    return SubscribeResponse(message="You're on the list — see you at kick-off.", email=email)
