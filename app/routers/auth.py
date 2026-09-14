"""Reader account endpoints.

Small, email + password + opaque session-token flow for the "Join SimplyUtd"
modal. The token is returned once and the browser keeps it in ``localStorage``;
subsequent calls send it as ``Authorization: Bearer <token>``.

Registering also adds the address to the newsletter list, because the modal has
always been the site's signup moment and doubling the two would surprise nobody.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from .. import db as db_module
from ..db import get_db
from ..schemas import AuthResponse, LoginCreate, MeResponse, RegisterCreate
from ..services import mailer, user_service
from ..utils import new_id, utcnow

logger = logging.getLogger("simplyutd.auth")

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip() or None
    return authorization.strip() or None


def _fail(exc: user_service.AuthError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


def _subscribe(database: Database, email: str, source: str) -> None:
    """Add the new account to the mailing list, never failing the signup."""
    try:
        if database[db_module.SUBSCRIBERS].find_one({"email": email}, {"_id": 1}):
            return
        database[db_module.SUBSCRIBERS].insert_one(
            {
                "id": new_id(),
                "email": email,
                "source": source,
                "created_at": utcnow(),
            }
        )
    except DuplicateKeyError:
        return
    except Exception:  # noqa: BLE001 - the account is already created
        logger.debug("Could not add %s to the newsletter list", email, exc_info=True)


@router.post("/register", response_model=AuthResponse, status_code=201)
def register(payload: RegisterCreate, database: Database = Depends(get_db)) -> AuthResponse:
    try:
        user, token = user_service.register(
            database,
            email=str(payload.email),
            password=payload.password,
            name=payload.name,
        )
    except user_service.AuthError as exc:
        raise _fail(exc) from exc

    _subscribe(database, user["email"], "account")
    try:
        mailer.send_welcome_email(user["email"])
        mailer.send_signup_notification(user["email"])
    except Exception:  # noqa: BLE001 - email is best-effort
        logger.exception("Signup email dispatch failed")

    return AuthResponse(token=token, user=user)


@router.post("/login", response_model=AuthResponse)
def login(payload: LoginCreate, database: Database = Depends(get_db)) -> AuthResponse:
    try:
        user, token = user_service.authenticate(
            database, email=str(payload.email), password=payload.password
        )
    except user_service.AuthError as exc:
        raise _fail(exc) from exc
    return AuthResponse(token=token, user=user)


@router.get("/me", response_model=MeResponse)
def me(
    authorization: str | None = Header(default=None),
    database: Database = Depends(get_db),
) -> MeResponse:
    """Bootstrap the signed-in state on page load. 200 with ``user: null`` when
    the token is missing or stale, so the client never has to special-case 401."""
    user = user_service.user_for_token(database, _bearer(authorization))
    return MeResponse(user=user)


@router.post("/logout")
def logout(
    authorization: str | None = Header(default=None),
    database: Database = Depends(get_db),
) -> dict:
    return {"ok": True, "revoked": user_service.logout(database, _bearer(authorization))}
