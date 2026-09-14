"""Link-preview metadata for feed entries that publish no summary.

Some publishers (for example Sky Sports and ESPN) ship RSS items with only a
title and a link. Rather than scraping the article body — which would mean
republishing copyrighted text — we read the page's *link-preview* metadata:
``og:description`` / ``<meta name="description">`` and ``og:image``. Publishers
expose these specifically so third parties can show a short preview with a link
back, so the excerpt is kept short and is always displayed next to the link to
the publisher's own article.

The whole feature is optional (``NEWS_FETCH_EXCERPTS``) and capped per ingest run
(``NEWS_EXCERPT_MAX``) so a slow publisher can never stall ingestion.
"""
from __future__ import annotations

import logging
import re
from html import unescape
from urllib.parse import urljoin

import httpx

from ..config import settings
from ..utils import strip_html

logger = logging.getLogger("simplyutd.excerpt")

# Only the head of a document is needed for meta tags, and we never want to pull
# a whole article page into memory.
_MAX_HTML = 300_000
_META_KEYS = ("og:description", "twitter:description", "description")
_IMAGE_KEYS = ("og:image", "twitter:image")

_TAG_RE = re.compile(r"<meta\s+([^>]+?)/?>", re.IGNORECASE)
_ATTR_RE = re.compile(r"([a-zA-Z:_.-]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')")


def _meta_map(html: str) -> dict[str, str]:
    """``{property-or-name: content}`` for every ``<meta>`` tag, in priority order."""
    found: dict[str, str] = {}
    for tag in _TAG_RE.findall(html[:_MAX_HTML]):
        attrs: dict[str, str] = {}
        for match in _ATTR_RE.finditer(tag):
            value = match.group(2) if match.group(2) is not None else match.group(3)
            attrs[match.group(1).lower()] = value
        name = (attrs.get("property") or attrs.get("name") or "").lower()
        content = (attrs.get("content") or "").strip()
        if name and content and name not in found:
            found[name] = unescape(content)
    return found


def _meta_value(html: str, keys: tuple[str, ...]) -> str | None:
    """Value of the highest-priority key present in the document."""
    found = _meta_map(html)
    for key in keys:
        if found.get(key):
            return found[key]
    return None


def fetch_preview(url: str, *, timeout: float | None = None) -> dict[str, str | None]:
    """Return ``{description, image}`` scraped from the article's link preview."""
    if not url:
        return {"description": None, "image": None}
    try:
        response = httpx.get(
            url,
            timeout=timeout or settings.news_excerpt_timeout,
            follow_redirects=True,
            headers={
                "User-Agent": settings.rss_user_agent,
                "Accept": "text/html,application/xhtml+xml",
            },
        )
    except Exception:  # noqa: BLE001 - a slow publisher must not break ingestion
        logger.debug("Excerpt fetch failed: %s", url)
        return {"description": None, "image": None}

    if response.status_code >= 400:
        return {"description": None, "image": None}

    html = response.text
    description = _meta_value(html, _META_KEYS)
    image = _meta_value(html, _IMAGE_KEYS)
    if description:
        description = strip_html(description)[: settings.news_excerpt_length]
    if image:
        image = urljoin(str(response.url), image)
    return {"description": description or None, "image": image}


def enrich(entries: list, *, cap: int | None = None) -> int:
    """Fill in missing summaries/images from link-preview metadata in place.

    Only entries whose summary is shorter than ``NEWS_EXCERPT_MIN_SUMMARY`` are
    touched, and at most ``cap`` (default ``NEWS_EXCERPT_MAX``) lookups happen per
    run. Returns the number of entries updated.
    """
    if not settings.news_fetch_excerpts:
        return 0

    budget = settings.news_excerpt_max if cap is None else cap
    if budget <= 0:
        return 0

    updated = 0
    for entry in entries:
        if budget <= 0:
            break
        if len(entry.summary or "") >= settings.news_excerpt_min_summary:
            continue
        budget -= 1
        preview = fetch_preview(entry.link)
        if preview["description"]:
            entry.summary = preview["description"]
        if preview["image"] and not entry.image:
            entry.image = preview["image"]
        if preview["description"] or preview["image"]:
            updated += 1
    if updated:
        logger.info("Enriched %d entries from link previews", updated)
    return updated
