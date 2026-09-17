"""Cloudinary storage for images and files.

When Cloudinary credentials are absent the service degrades gracefully:
uploads return ``None`` and callers keep the original (remote) URL.
"""
from __future__ import annotations

import logging
from typing import Any, BinaryIO

import cloudinary
import cloudinary.uploader
from cloudinary.utils import cloudinary_url

from ..config import settings
from ..utils import new_id

logger = logging.getLogger("simplyutd.cloudinary")

_configured = False

#: Short description of the most recent mirror failure, or ``None``. Mirrors are
#: best-effort, but a silent 0% success rate hides a misconfigured account, so
#: the reason is kept here and reported by ``/api/diag`` and the ingest summary.
_last_error: str | None = None


def is_configured() -> bool:
    return settings.cloudinary_configured


def last_error() -> str | None:
    """Why the last mirror attempt failed, if it did."""
    return _last_error


def _record_error(exc: BaseException) -> None:
    global _last_error
    message = str(exc).strip().replace("\n", " ")
    _last_error = f"{type(exc).__name__}: {message}"[:240] or type(exc).__name__


def _clear_error() -> None:
    """Reset the reported failure so it always describes the latest attempt."""
    global _last_error
    _last_error = None


def _ensure_config() -> bool:
    global _configured
    if _configured:
        return True
    if not settings.cloudinary_configured:
        return False
    cloudinary.config(
        cloud_name=settings.cloudinary_cloud_name,
        api_key=settings.cloudinary_api_key,
        api_secret=settings.cloudinary_api_secret,
        secure=True,
    )
    _configured = True
    return True


def upload_bytes(
    data: bytes,
    *,
    folder: str = "uploads",
    public_id: str | None = None,
    resource_type: str = "image",
    tags: list[str] | None = None,
) -> dict[str, Any] | None:
    """Upload raw bytes to Cloudinary. Returns the result dict or ``None``."""
    if not _ensure_config():
        logger.info("Cloudinary not configured — skipping upload of %d bytes", len(data))
        return None
    target_folder = f"{settings.cloudinary_folder}/{folder}".strip("/")
    try:
        return cloudinary.uploader.upload(
            data,
            folder=target_folder,
            public_id=public_id or new_id(),
            resource_type=resource_type,
            tags=tags,
            overwrite=False,
        )
    except Exception as exc:  # noqa: BLE001 - never let storage break a request
        _record_error(exc)
        logger.exception("Cloudinary upload failed (folder=%s)", target_folder)
        return None


def upload_fileobj(
    fileobj: BinaryIO,
    *,
    folder: str = "uploads",
    public_id: str | None = None,
    resource_type: str = "image",
    tags: list[str] | None = None,
) -> dict[str, Any] | None:
    """Upload a file-like object to Cloudinary."""
    if not _ensure_config():
        return None
    target_folder = f"{settings.cloudinary_folder}/{folder}".strip("/")
    try:
        return cloudinary.uploader.upload(
            fileobj,
            folder=target_folder,
            public_id=public_id or new_id(),
            resource_type=resource_type,
            tags=tags,
            overwrite=False,
        )
    except Exception as exc:  # noqa: BLE001
        _record_error(exc)
        logger.exception("Cloudinary file upload failed (folder=%s)", target_folder)
        return None


def upload_remote(
    url: str, *, folder: str = "news", public_id: str | None = None
) -> str | None:
    """Fetch a remote image and mirror it to Cloudinary.

    Returns the secure Cloudinary URL on success, otherwise ``None`` so the
    caller can fall back to the original URL.
    """
    if not _ensure_config():
        return None
    if not url or not url.startswith(("http://", "https://")):
        return None
    target_folder = f"{settings.cloudinary_folder}/{folder}".strip("/")
    try:
        result = cloudinary.uploader.upload(
            url,
            folder=target_folder,
            public_id=public_id or new_id(),
            resource_type="image",
            overwrite=False,
        )
        _clear_error()
        return result.get("secure_url") or result.get("url")
    except Exception as exc:  # noqa: BLE001 - remote hot-link may be blocked
        # Publishers that block hot-linking, an unverified account, or a wrong
        # API secret all land here; without the traceback there is no way to
        # tell them apart from the logs.
        _record_error(exc)
        logger.warning("Cloudinary remote mirror failed for %s: %s", url, exc)
        logger.debug("Cloudinary remote mirror traceback", exc_info=True)
        return None


def build_url(public_id: str, **options: Any) -> str | None:
    """Build a delivery URL for an existing asset."""
    if not _ensure_config():
        return None
    url, _ = cloudinary_url(public_id, secure=True, **options)
    return url


def destroy(public_id: str, resource_type: str = "image") -> bool:
    if not _ensure_config():
        return False
    try:
        cloudinary.uploader.destroy(public_id, resource_type=resource_type)
        return True
    except Exception:  # noqa: BLE001
        logger.exception("Cloudinary destroy failed for %s", public_id)
        return False
