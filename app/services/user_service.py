"""Reader accounts and sessions.

The site lets a visitor create an account so they can post comments under a
stable name instead of a random guest handle. Everything here is standard
library — PBKDF2-HMAC-SHA256 for password hashing and :mod:`secrets` for session
tokens — so the service gains real accounts without pulling in a new dependency
or a heavyweight auth framework.

Passwords are stored as ``iterations$salt_hex$hash_hex`` and compared with
:func:`hmac.compare_digest`, so verification is constant-time.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from .. import db as db_module
from ..utils import new_id, serialize, utcnow

logger = logging.getLogger("simplyutd.users")

PBKDF2_ITERATIONS = 260_000
SALT_BYTES = 16
SESSION_TTL_DAYS = 30
MIN_PASSWORD_LENGTH = 8


class AuthError(Exception):
    """Raised for any credential problem; the router turns it into a 400/401."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"{PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    """Constant-time check of ``password`` against a stored hash."""
    if not stored:
        return False
    try:
        iterations_raw, salt_hex, digest_hex = str(stored).split("$")
        iterations = int(iterations_raw)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, TypeError):
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return hmac.compare_digest(candidate, expected)


def _normalise_email(email: str) -> str:
    return str(email).strip().lower()


def _display_name(name: str | None, email: str) -> str:
    cleaned = (name or "").strip()
    if cleaned:
        return cleaned[:60]
    return email.split("@")[0][:60] or "Red Devil"


def _validate_password(password: str) -> None:
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise AuthError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters.", status_code=400
        )


def public_user(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    """The user shape sent to the browser — never includes the password hash."""
    if not doc:
        return None
    return {
        "id": doc.get("id"),
        "email": doc.get("email"),
        "name": doc.get("name"),
        "initials": doc.get("initials") or "RD",
        "created_at": doc.get("created_at"),
    }


def _initials(name: str) -> str:
    parts = [part for part in name.replace("_", " ").split() if part]
    if not parts:
        return "RD"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _safe(doc: dict[str, Any]) -> dict[str, Any]:
    """Serialise a user document, dropping the password hash."""
    clean = {key: value for key, value in doc.items() if key != "password"}
    return serialize(clean)


def register(
    database: Database, *, email: str, password: str, name: str | None = None
) -> tuple[dict[str, Any], str]:
    """Create an account and return ``(user, session_token)``."""
    normalised = _normalise_email(email)
    if "@" not in normalised or len(normalised) < 5:
        raise AuthError("Enter a valid email address.", status_code=400)
    _validate_password(password)

    existing = database[db_module.USERS].find_one({"email": normalised}, {"_id": 1})
    if existing:
        raise AuthError("An account with that email already exists.", status_code=409)

    display = _display_name(name, normalised)
    doc = {
        "id": new_id(),
        "email": normalised,
        "name": display,
        "initials": _initials(display),
        "password": hash_password(password),
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    try:
        database[db_module.USERS].insert_one(doc)
    except DuplicateKeyError:
        # Lost a race with a concurrent registration.
        raise AuthError("An account with that email already exists.", status_code=409) from None

    token = create_session(database, doc["id"])
    return _safe(doc), token


def authenticate(database: Database, *, email: str, password: str) -> tuple[dict[str, Any], str]:
    """Check credentials and open a session, returning ``(user, token)``."""
    normalised = _normalise_email(email)
    doc = database[db_module.USERS].find_one({"email": normalised})
    if doc is None or not verify_password(password, doc.get("password")):
        # One message for both cases so the endpoint is not an email oracle.
        raise AuthError("That email and password don't match.", status_code=401)
    token = create_session(database, doc["id"])
    return _safe(doc), token


def create_session(database: Database, user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    database[db_module.SESSIONS].insert_one(
        {
            "id": new_id(),
            "token": token,
            "user_id": user_id,
            "created_at": utcnow(),
            "expires_at": utcnow() + timedelta(days=SESSION_TTL_DAYS),
        }
    )
    return token


def user_for_token(database: Database, token: str | None) -> dict[str, Any] | None:
    """Resolve a bearer token to a user, dropping expired sessions on the way."""
    if not token:
        return None
    session = database[db_module.SESSIONS].find_one({"token": token})
    if session is None:
        return None
    expires = session.get("expires_at")
    if isinstance(expires, datetime):
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < utcnow():
            database[db_module.SESSIONS].delete_one({"token": token})
            return None
    user = database[db_module.USERS].find_one({"id": session["user_id"]})
    return _safe(user) if user else None


def logout(database: Database, token: str | None) -> bool:
    if not token:
        return False
    result = database[db_module.SESSIONS].delete_one({"token": token})
    return bool(result.deleted_count)
