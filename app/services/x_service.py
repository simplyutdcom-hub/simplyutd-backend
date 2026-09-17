"""@SimplyUtd's X timeline, read through a pool of Nitter RSS mirrors.

X's embed widget and its public syndication endpoints either want a paid key or
rate limit the server, so the home page's "Live United" panel is fed from
Nitter - an unofficial third-party mirror that renders a profile as RSS.

Instances come and go, so a pool is tried in order and the first instance that
returns a readable feed wins; the read is then cached because every visit to the
home page asks for it. The panel polls for new posts, so the cache is served
while it is fresh, refreshed in the background while it is merely stale, and
only read through to a mirror once it has expired. Like the other upstream
integrations this never raises: a total failure returns what is cached, or
nothing, and the panel shows an empty state. Nitter exposes no engagement
counts, so a post carries its text, author, media and timestamp only.
"""
from __future__ import annotations

import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any, Callable, Iterator
from urllib.parse import unquote

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

_DC_CREATOR = "{http://purl.org/dc/elements/1.1/}creator"
_PIC_MARKER = "/pic/"
_STATUS_RE = re.compile(r"/status/(\d+)")
_STATUS_AUTHOR_RE = re.compile(r"/([A-Za-z0-9_]+)/status/\d+")
_RETWEET_RE = re.compile(r"^RT by @([A-Za-z0-9_]+):\s*")
_PINNED_PREFIX = "Pinned: "
_BLOCKQUOTE_RE = re.compile(r"<blockquote>(.*?)</blockquote>", re.S | re.I)
_PARAGRAPH_RE = re.compile(r"<p>(.*?)</p>", re.S | re.I)
_IMAGE_RE = re.compile(r"<img[^>]+src=\"([^\"]+)\"", re.I)
_QUOTE_AUTHOR_RE = re.compile(r"^<b>([^<]*)</b>", re.I)
_BREAK_RE = re.compile(r"<br\s*/?>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_FXTWITTER_AVATAR_RE = re.compile(r"_normal(\.\w+)$")


def _plain_text(fragment: str) -> str:
    """Flatten an HTML fragment to text, keeping line breaks but not markup."""
    text = _BREAK_RE.sub("\n", fragment)
    text = _TAG_RE.sub("", text)
    text = unescape(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _media_url(src: str) -> str | None:
    """Map a Nitter image proxy URL onto the underlying Twitter CDN URL.

    Nitter rewrites every image it shows as ``<instance>/pic/<encoded>``, where
    the encoded part is either a full host path (``pbs.twimg.com/media/x.jpg``)
    or a path relative to the media CDN (``media/x.jpg``).
    """
    cleaned = (src or "").strip()
    if not cleaned:
        return None
    _, marker, tail = cleaned.partition(_PIC_MARKER)
    if not marker:
        return cleaned
    path = unquote(tail)
    if path.startswith("http"):
        return path
    head = path.split("/", 1)[0]
    if "." in head:  # already a host such as pbs.twimg.com
        return f"https://{path}"
    return f"https://pbs.twimg.com/{path}"


def _media(fragment: str) -> list[dict[str, str]]:
    """The images and video thumbnails attached to a post."""
    media: list[dict[str, str]] = []
    for src in _IMAGE_RE.findall(fragment):
        url = _media_url(src)
        if not url:
            continue
        kind = "video" if "video_thumb" in url else "image"
        media.append({"type": kind, "url": url})
    return media


def _post_url(url: str) -> tuple[str | None, str | None]:
    """``(canonical x.com URL, status id)`` for a Nitter item link."""
    match = _STATUS_RE.search(url or "")
    if not match:
        return None, None
    author = _STATUS_AUTHOR_RE.search(url)
    handle = author.group(1) if author else settings.x_handle
    return f"https://x.com/{handle}/status/{match.group(1)}", match.group(1)


def _created_at(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


@dataclass
class ParsedFeed:
    """A Nitter profile feed: who it belongs to, and their posts."""

    handle: str | None = None
    name: str | None = None
    avatar: str | None = None
    posts: list[dict[str, Any]] = field(default_factory=list)


def _quoted(fragment: str | None) -> dict[str, Any] | None:
    """The quoted post Nitter appends below a post, if there is one."""
    if not fragment:
        return None
    match = _QUOTE_AUTHOR_RE.match(fragment.strip())
    label = match.group(1).strip() if match else ""
    author_name = label or None
    author = None
    handle_match = re.search(r"\(@([A-Za-z0-9_]+)\)", label)
    if handle_match:
        author = handle_match.group(1)
        author_name = label[: handle_match.start()].strip() or None
    body = fragment[match.end() :] if match else fragment
    url, _ = _post_url(body)
    return {
        "author": author,
        "author_name": author_name,
        "url": url,
        "text": "\n\n".join(_plain_text(chunk) for chunk in _PARAGRAPH_RE.findall(body)).strip(),
        "media": _media(body),
    }


def parse_feed(xml_text: str, handle: str | None = None) -> ParsedFeed:
    """Normalise a Nitter RSS document into the panel's post shape."""
    feed = ParsedFeed()
    try:
        channel = ET.fromstring(xml_text).find("channel")
    except ET.ParseError:
        logger.debug("Nitter feed was not valid XML")
        return feed
    if channel is None:
        return feed

    feed.handle = handle or settings.x_handle
    title = (channel.findtext("title") or "").strip()
    if " / " in title:
        feed.name, _, handle_part = title.partition(" / ")
        feed.name = feed.name.strip() or None
        feed.handle = handle_part.strip().lstrip("@") or feed.handle
    avatar = channel.findtext("image/url")
    feed.avatar = _media_url(avatar) if avatar else None

    for entry in channel.findall("item"):
        title_text = (entry.findtext("title") or "").strip()
        body = entry.findtext("description") or ""
        link = (entry.findtext("link") or "").strip()

        is_pinned = title_text.startswith(_PINNED_PREFIX)
        quoted_html: str | None = None
        main_html = body
        quote_match = _BLOCKQUOTE_RE.search(body)
        if quote_match:
            main_html = body[: quote_match.start()]
            quoted_html = quote_match.group(1)
        if is_pinned:
            title_text = title_text[len(_PINNED_PREFIX) :].strip()

        retweeted_by = None
        retweet_match = _RETWEET_RE.match(title_text)
        if retweet_match:
            retweeted_by = retweet_match.group(1)
            title_text = title_text[retweet_match.end() :].strip()

        text = "\n\n".join(_plain_text(chunk) for chunk in _PARAGRAPH_RE.findall(main_html))
        text = text.strip() or title_text

        url, status_id = _post_url(link)
        author_match = _STATUS_AUTHOR_RE.search(link)
        author = author_match.group(1) if author_match else feed.handle
        guid = (entry.findtext("guid") or "").strip()
        creator = (entry.findtext(_DC_CREATOR) or "").strip().lstrip("@")

        feed.posts.append(
            {
                "id": status_id or guid or url,
                "url": url,
                "text": text,
                "author": author,
                "author_avatar": None,
                "created_at": _created_at(entry.findtext("pubDate")),
                "media": _media(main_html),
                "quoted": _quoted(quoted_html),
                "is_retweet": retweeted_by is not None,
                "retweeted_by": retweeted_by,
                "is_pinned": is_pinned,
                "is_reply": text.startswith("@"),
                "tweeted_by": creator or author,
            }
        )

    # A profile leads with its pinned post (as X does), then the timeline, which
    # Nitter already returns newest first.
    def newest_first(post: dict[str, Any]) -> str:
        return post["created_at"] or ""

    pinned = [post for post in feed.posts if post["is_pinned"]]
    rest = sorted((post for post in feed.posts if not post["is_pinned"]), key=newest_first, reverse=True)
    feed.posts = pinned + rest
    return feed


@dataclass
class _TimelineCache:
    posts: list[dict[str, Any]] | None = None
    handle: str | None = None
    name: str | None = None
    avatar: str | None = None
    expires: float = 0.0
    # When the cached read stops being "fresh enough" and a background refresh
    # becomes worthwhile, even though it is still being served.
    revalidate_at: float = 0.0
    fetched_at: float | None = None
    source: str | None = None

    profile: dict[str, Any] | None = None
    profile_expires: float = 0.0


_cache = _TimelineCache()
# Guards the background refresher so a burst of polls triggers one mirror read.
_refreshing = False


def reset_cache() -> None:
    """Clear the cached timeline and profile (used by tests)."""
    global _refreshing
    _refreshing = False
    _cache.posts = None
    _cache.profile = None
    _cache.expires = 0.0
    _cache.revalidate_at = 0.0
    _cache.profile_expires = 0.0
    _cache.fetched_at = None
    _cache.source = None
    _media_cache.clear()


def _get(url: str, accept: str) -> str | None:
    try:
        response = httpx.get(
            url,
            timeout=settings.x_http_timeout,
            follow_redirects=True,
            headers={"User-Agent": settings.x_user_agent, "Accept": accept},
        )
    except Exception:  # noqa: BLE001 - a dead mirror must not break the page
        logger.debug("X feed fetch failed: %s", url)
        return None
    if response.status_code >= 400:
        logger.debug("X feed fetch returned %s: %s", response.status_code, url)
        return None
    return response.text or None


# --- Playable media -------------------------------------------------------- #

# Nitter's RSS only carries a video's poster frame, so the playable file is
# looked up on demand - when a reader actually hits play - and then remembered,
# because a post's media never changes and fxtwitter is a shared free service.
_media_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_MEDIA_CACHE_SECONDS = 86_400
_POST_ID_RE = re.compile(r"^\d{1,25}$")


def _video(item: dict[str, Any]) -> dict[str, Any] | None:
    """The best progressive MP4 for one fxtwitter media entry."""
    if item.get("type") not in {"video", "gif"}:
        return None
    candidates = [
        variant
        for variant in item.get("formats") or []
        if variant.get("container") == "mp4" and variant.get("url")
    ]
    best = max(candidates, key=lambda v: v.get("bitrate") or 0, default=None)
    url = (best or {}).get("url") or item.get("url")
    if not url:
        return None
    return {
        "url": url,
        "thumbnail": item.get("thumbnail_url") or None,
        "duration": item.get("duration"),
        "width": item.get("width"),
        "height": item.get("height"),
        "alt": item.get("alt_text") or None,
    }


def fetch_media(post_id: str, force: bool = False) -> dict[str, Any]:
    """Playable video URLs for one post, keyed by status id."""
    post_id = (post_id or "").strip()
    if not _POST_ID_RE.match(post_id):
        return {"id": post_id, "videos": []}

    cached = _media_cache.get(post_id)
    if cached and not force and time.time() < cached[0]:
        return cached[1]

    payload = {"id": post_id, "videos": []}
    raw = _get(f"https://api.fxtwitter.com/{settings.x_handle}/status/{post_id}", "application/json")
    if raw:
        try:
            media = ((json.loads(raw) or {}).get("tweet") or {}).get("media") or {}
            for item in media.get("all") or []:
                video = _video(item)
                if video:
                    payload["videos"].append(video)
        except (ValueError, AttributeError, TypeError):
            logger.debug("X media payload unreadable for %s", post_id)

    if payload["videos"]:
        _media_cache[post_id] = (time.time() + _MEDIA_CACHE_SECONDS, payload)
    return payload


def video_source(post_id: str) -> str | None:
    """The direct MP4 for one post, if it has one."""
    videos = fetch_media(post_id).get("videos") or []
    return videos[0].get("url") if videos else None


# Twitter's video CDN answers 403 to any request carrying a Referer, and a
# browser always sends one for a <video> on our page - so the player could never
# load the file direct. The bytes are relayed from the server instead: httpx
# sends no Referer, which is exactly what the CDN wants.
_STREAM_CHUNK = 65_536
# Only these travel back to the player; the CDN's CORS and cookie headers do not
# apply to a same-backend request and would only confuse the browser.
_RELAY_HEADERS = (
    "content-type",
    "content-length",
    "content-range",
    "accept-ranges",
    "etag",
    "last-modified",
)


@dataclass
class UpstreamVideo:
    """An open upstream video response, relayed to the reader chunk by chunk."""

    status_code: int
    headers: dict[str, str]
    chunks: Iterator[bytes]
    release: Callable[[], None]

    def close(self) -> None:
        self.release()


def open_video_stream(url: str, range_header: str | None = None) -> UpstreamVideo | None:
    """Open a video for relaying, forwarding the reader's Range request.

    Returns ``None`` when the file cannot be opened, so the route can answer
    with a clean error instead of a half-written body.
    """
    request_headers = {"User-Agent": settings.x_user_agent, "Accept": "*/*"}
    if range_header:
        request_headers["Range"] = range_header

    client = httpx.Client(
        timeout=httpx.Timeout(settings.x_http_timeout, read=30.0),
        follow_redirects=True,
    )
    try:
        upstream = client.send(
            client.build_request("GET", url, headers=request_headers), stream=True
        )
    except Exception:  # noqa: BLE001 - an unreachable CDN is a clean failure
        client.close()
        logger.debug("X video stream failed to open: %s", url)
        return None

    if upstream.status_code >= 400:
        upstream.close()
        client.close()
        logger.debug("X video stream returned %s: %s", upstream.status_code, url)
        return None

    def release() -> None:
        upstream.close()
        client.close()

    def relay() -> Iterator[bytes]:
        try:
            yield from upstream.iter_bytes(_STREAM_CHUNK)
        finally:
            release()

    headers = {
        key.lower(): value
        for key, value in upstream.headers.items()
        if key.lower() in _RELAY_HEADERS
    }
    headers.setdefault("cache-control", "public, max-age=86400")
    return UpstreamVideo(upstream.status_code, headers, relay(), release)


def fetch_timeline(force: bool = False) -> ParsedFeed:
    """The most recent posts, cached for ``x_cache_seconds``.

    Only a readable, non-empty feed is cached, so a mirror that is briefly down
    is retried on the next request instead of freezing an empty panel.
    """
    now = time.monotonic()
    if not force and _cache.posts is not None and now < _cache.expires:
        return ParsedFeed(
            handle=_cache.handle,
            name=_cache.name,
            avatar=_cache.avatar,
            posts=_cache.posts,
        )

    for base in settings.x_rss_instances:
        url = f"{base.rstrip('/')}/{settings.x_handle}/rss"
        body = _get(url, "application/rss+xml, application/xml, text/xml")
        if not body:
            continue
        parsed = parse_feed(body)
        if not parsed.posts:
            continue
        _cache.posts = parsed.posts
        _cache.handle = parsed.handle
        _cache.name = parsed.name
        _cache.avatar = parsed.avatar
        _cache.source = httpx.URL(url).host
        _cache.expires = now + settings.x_cache_seconds
        _cache.revalidate_at = now + settings.x_revalidate_seconds
        _cache.fetched_at = time.time()
        return parsed

    if _cache.posts is not None:  # serve a stale read rather than nothing
        return ParsedFeed(
            handle=_cache.handle,
            name=_cache.name,
            avatar=_cache.avatar,
            posts=_cache.posts,
        )
    return ParsedFeed(handle=settings.x_handle)


def revalidate_due() -> bool:
    """True when the served read is old enough to be worth refreshing off-thread.

    False while nothing is cached: that request reads through to a mirror itself.
    """
    return _cache.posts is not None and time.monotonic() >= _cache.revalidate_at


def revalidate() -> None:
    """Refresh the cached timeline out of band, so the next poll finds it fresh.

    Runs after the response has been sent, which is why a reader never waits for
    a mirror. Safe to call from every poll: the guard plus the backoff below keep
    upstream traffic at one read per ``x_revalidate_seconds``.
    """
    global _refreshing
    if _refreshing or not revalidate_due():
        return
    _refreshing = True
    try:
        fetch_timeline(force=True)
    finally:
        _refreshing = False
        if time.monotonic() >= _cache.revalidate_at:
            # The refresh did not land (every mirror refused), so wait a full
            # window before trying again rather than retrying on every poll.
            _cache.revalidate_at = time.monotonic() + settings.x_revalidate_seconds


def fetch_profile(force: bool = False) -> dict[str, Any]:
    """The account card (name, bio, followers) used for the panel header."""
    now = time.monotonic()
    if not force and _cache.profile is not None and now < _cache.profile_expires:
        return _cache.profile

    body = _get(f"https://api.fxtwitter.com/{settings.x_handle}", "application/json")
    if not body:
        return _cache.profile or {}
    try:
        payload = json.loads(body)
        user = payload.get("user") or {}
    except (ValueError, AttributeError):
        return _cache.profile or {}
    if not user:
        return _cache.profile or {}

    avatar = str(user.get("avatar_url") or "")
    profile = {
        "handle": user.get("screen_name") or settings.x_handle,
        "name": user.get("name") or settings.x_handle,
        "bio": user.get("description") or None,
        "avatar": _FXTWITTER_AVATAR_RE.sub(r"_400x400\1", avatar) or None,
        "banner": user.get("banner_url") or None,
        "followers": user.get("followers"),
        "following": user.get("following"),
        "posts": user.get("tweets"),
        "url": user.get("url") or f"https://x.com/{settings.x_handle}",
        "verified": bool((user.get("verification") or {}).get("verified")),
    }
    _cache.profile = profile
    _cache.profile_expires = now + settings.x_profile_cache_seconds
    return profile


def timeline(force: bool = False, limit: int | None = None) -> dict[str, Any]:
    """Everything the Live United panel needs: the account and its recent posts."""
    feed = fetch_timeline(force=force)
    profile = fetch_profile(force=force)
    handle = profile.get("handle") or feed.handle or settings.x_handle
    avatar = profile.get("avatar") or feed.avatar

    posts: list[dict[str, Any]] = []
    for post in feed.posts:
        # Only the account's own avatar is known; a reposted author falls back to
        # a monogram in the UI.
        own = post["author"] == handle
        posts.append(
            {
                **post,
                "author_avatar": avatar if own else None,
                "author_name": post.get("author_name") or (feed.name if own else post["author"]),
            }
        )

    return {
        "handle": handle,
        "name": profile.get("name") or feed.name,
        "avatar": avatar,
        "profile": profile,
        "posts": posts[:limit] if limit else posts,
        "total": len(posts),
        "source": _cache.source,
        "updated_at": (
            datetime.fromtimestamp(_cache.fetched_at, tz=timezone.utc).isoformat()
            if _cache.fetched_at
            else None
        ),
    }
