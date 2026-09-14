"""Public contact form + contact-page info."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pymongo.database import Database

from .. import db as db_module
from ..db import get_db
from ..schemas import ContactCreate, ContactResponse
from ..services import mailer
from ..services.seed import CONTACT_INFO
from ..utils import initials_from, new_id, utcnow

logger = logging.getLogger("simplyutd.contact")

router = APIRouter(prefix="/api/contact", tags=["contact"])


@router.get("/info")
def contact_info(database: Database = Depends(get_db)) -> dict:
    doc = database[db_module.META].find_one({"key": "contact_info"}, {"_id": 0})
    return doc.get("data", CONTACT_INFO) if doc else CONTACT_INFO


@router.post("", response_model=ContactResponse, status_code=201)
def submit_contact(payload: ContactCreate, database: Database = Depends(get_db)) -> ContactResponse:
    message_id = new_id()
    doc = {
        "id": message_id,
        "name": payload.name,
        "email": str(payload.email),
        "type": payload.type,
        "subject": payload.subject,
        "message": payload.message,
        "initials": initials_from(payload.name),
        "unread": True,
        "favourite": False,
        "label": None,
        "created_at": utcnow(),
    }
    database[db_module.MESSAGES].insert_one(doc)

    # Notify the team; failures are logged but never block the visitor.
    try:
        mailer.send_contact_notification(
            payload.name, str(payload.email), payload.type, payload.subject, payload.message
        )
        mailer.send_contact_autoreply(payload.name, str(payload.email))
    except Exception:  # noqa: BLE001
        logger.exception("Contact email dispatch failed")

    return ContactResponse(id=message_id)
