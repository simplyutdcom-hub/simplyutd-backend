"""RSS/Atom fetching and normalisation.

Pulls publicly available Manchester United news feeds and maps each entry to a
normalised record (title, summary, link, image, source, published date). Only
syndicated metadata is stored — a headline, a short summary and a link back to
the publisher — never the full copyrighted article body.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlparse

import feedparser
import httpx

from ..config import settings
from ..utils import parse_datetime, strip_html

logger = logging.getLogger("simplyutd.rss")

# Google News topic searches for Manchester United. These are RSS (not the
# copyrighted web page) and aggregate articles with attribution + link-back.
GOOGLE_NEWS_QUERIES = [
    "https://news.google.com/rss/search?q=Manchester+United&hl=en-GB&gl=GB&ceid=GB:en",
    "https://news.google.com/rss/search?q=%22Manchester+United%22+transfer&hl=en-GB&gl=GB&ceid=GB:en",
]

_IMAGE_KEYS = ("media_content", "media_thumbnail", "enclosures", "links")


@dataclass
class FeedEntry:
    """A normalised RSS entry ready to be persisted."""

    title: str
    link: str
    summary: str = ""
    image: str | None = None
    source: str = ""
    guid: str | None = None
    category: str = "News"
    tags: list[str] = field(default_factory=list)
    published_at: datetime | None = None
    external: bool = True


def _source_name(entry: dict, feed_title: str, link: str) -> str:
    source = entry.get("source")
    if isinstance(source, dict) and source.get("title"):
        return strip_html(source["title"])[:80]
    if feed_title:
        return strip_html(feed_title)[:80]
    try:
        return urlparse(link).netloc.replace("www.", "")[:80]
    except (ValueError, AttributeError):
        return "RSS"


def _first_image(entry: dict) -> str | None:
    """Extract the best available image URL from a feed entry."""
    for key in ("media_content", "media_thumbnail"):
        for media in entry.get(key) or []:
            url = media.get("url") if isinstance(media, dict) else None
            if url:
                return url
            # Some feeds nest the actual image one level deeper.
            for inner in media.get("content") or []:
                if isinstance(inner, dict) and inner.get("url"):
                    return inner["url"]

    for enclosure in entry.get("enclosures") or []:
        href = enclosure.get("href") or enclosure.get("url")
        mime = enclosure.get("type", "")
        if href and ("image" in mime or not mime):
            return href

    for link in entry.get("links") or []:
        if isinstance(link, dict) and link.get("rel") == "enclosure" and "image" in link.get("type", ""):
            return link.get("href")

    # Fall back to an <img> embedded in the summary/description HTML.
    import re

    html = entry.get("summary") or entry.get("description") or ""
    if html:
        match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def normalize_entry(entry: dict, feed_title: str = "", category: str = "News") -> FeedEntry | None:
    title = strip_html(entry.get("title", ""))
    link = entry.get("link") or ""
    if not title or not link:
        return None
    summary_html = entry.get("summary") or entry.get("description") or ""
    summary = strip_html(summary_html)
    tags = [strip_html(t.get("term", "")) for t in (entry.get("tags") or []) if isinstance(t, dict)]
    tags = [t for t in tags if t][:8]
    return FeedEntry(
        title=title[:300],
        link=link,
        summary=summary[:1200],
        image=_first_image(entry),
        source=_source_name(entry, feed_title, link),
        guid=entry.get("id") or entry.get("guid") or link,
        category=category,
        tags=tags,
        published_at=parse_datetime(
            entry.get("published_parsed")
            or entry.get("updated_parsed")
            or entry.get("published")
            or entry.get("updated")
        ),
        external=True,
    )


def fetch_feed(url: str, timeout: float = 20.0) -> bytes | None:
    """Fetch a feed URL, returning raw bytes (or ``None`` on failure)."""
    try:
        response = httpx.get(
            url,
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": settings.rss_user_agent,
                "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
            },
        )
    except Exception:  # noqa: BLE001 - a dead feed must not break ingestion
        logger.warning("Failed to fetch feed: %s", url)
        return None
    if response.status_code >= 400:
        logger.warning("Feed %s returned HTTP %s", url, response.status_code)
        return None
    return response.content


def parse_feed(url: str, *, limit: int | None = None, category: str = "News") -> list[FeedEntry]:
    """Fetch and parse a single feed into normalised entries."""
    raw = fetch_feed(url)
    if not raw:
        return []
    return parse_bytes(url, raw, limit=limit, category=category)


def parse_bytes(url: str, raw: bytes, *, limit: int | None = None, category: str = "News") -> list[FeedEntry]:
    """Parse already-fetched feed bytes into normalised entries."""
    parsed = feedparser.parse(raw)
    feed_title = strip_html(parsed.feed.get("title", "")) if parsed.feed else ""
    entries: list[FeedEntry] = []
    for entry in parsed.entries:
        normalised = normalize_entry(entry, feed_title, category)
        if normalised is not None:
            entries.append(normalised)
        if limit and len(entries) >= limit:
            break
    return entries


def collect_entries(
    feeds: list[str] | None = None,
    *,
    limit_per_feed: int | None = None,
    include_google_news: bool = True,
) -> list[FeedEntry]:
    """Fetch and normalise entries from all configured feeds (plus Google News)."""
    urls = list(feeds) if feeds is not None else list(settings.rss_feeds)
    if include_google_news and feeds is None:
        urls.extend(GOOGLE_NEWS_QUERIES)
    limit = limit_per_feed or settings.news_max_per_feed

    collected: list[FeedEntry] = []
    for url in urls:
        entries = parse_feed(url, limit=limit)
        logger.info("Feed %s -> %d entries", url, len(entries))
        collected.extend(entries)
    return collected
