"""Shared helpers: id generation, slugs, dates and document serialisation."""
from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_TAG_RE = re.compile(r"<[^>]+>")


def new_id() -> str:
    """Return a short, URL-safe unique identifier."""
    return uuid.uuid4().hex


def utcnow() -> datetime:
    """Timezone-aware current UTC time."""
    return datetime.now(timezone.utc)


def slugify(value: str, fallback: str | None = None) -> str:
    """Turn arbitrary text into a URL-friendly slug."""
    normalised = unicodedata.normalize("NFKD", value or "")
    ascii_only = normalised.encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_RE.sub("-", ascii_only.lower()).strip("-")
    if not slug:
        slug = (fallback or new_id())[:60]
    return slug[:80]


def strip_html(value: str | None) -> str:
    """Remove HTML tags and collapse whitespace (for RSS summaries)."""
    if not value:
        return ""
    text = _TAG_RE.sub(" ", value)
    text = (
        text.replace("&amp;", "&")
        .replace("&nbsp;", " ")
        .replace("&#8217;", "\u2019")
        .replace("&#8216;", "\u2018")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    return re.sub(r"\s+", " ", text).strip()


def initials_from(name: str) -> str:
    """Compute the initials used by the admin inbox UI ("Sarah Thompson" -> ST)."""
    parts = [p for p in re.split(r"\s+", (name or "").strip()) if p]
    if not parts:
        return "??"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def parse_datetime(value: Any) -> datetime | None:
    """Best-effort parse of RSS/ISO datetime values into aware datetimes."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        # feedparser gives struct_time; convert via datetime when possible.
        from time import mktime

        return datetime.fromtimestamp(mktime(value), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        pass
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        for parser in (_from_isoformat, _from_email):
            parsed = parser(text)
            if parsed is not None:
                return parsed
    return None


def _from_isoformat(text: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _from_email(text: str) -> datetime | None:
    from email.utils import parsedate_to_datetime

    try:
        dt = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def serialize(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    """Prepare a Mongo document for JSON output (drop ``_id``)."""
    if doc is None:
        return None
    out = {k: v for k, v in doc.items() if k != "_id"}
    return out


def serialize_many(docs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [serialize(doc) for doc in docs if doc is not None]  # type: ignore[misc]
