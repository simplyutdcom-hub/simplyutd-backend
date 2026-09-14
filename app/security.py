"""Admin authentication.

The whole ``/api/admin`` surface is protected by a single shared secret passed
in the ``X-Admin-Key`` header. The key is compared in constant time against
``ADMIN_API_KEY``. When the key is not configured, admin routes are disabled
rather than left open.
"""
from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, status

from .config import settings


def is_admin_key(candidate: str | None) -> bool:
    """Constant-time check used where a missing key is not an error."""
    if not settings.admin_api_key or not candidate:
        return False
    return secrets.compare_digest(candidate, settings.admin_api_key)


def require_admin(
    x_admin_key: str | None = Header(default=None, alias=settings.admin_key_header),
) -> None:
    """FastAPI dependency that gates admin endpoints."""
    if not settings.admin_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin API is disabled: ADMIN_API_KEY is not configured.",
        )
    if not is_admin_key(x_admin_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing admin key.",
        )
