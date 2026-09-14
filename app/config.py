"""Application configuration loaded from environment variables.

All settings are read once at import time. ``backend/.env`` is loaded if present
so the service works locally without exporting variables manually.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _list(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


class Settings:
    """Runtime settings for the SimplyUtd API."""

    def __init__(self) -> None:
        # --- Core ---------------------------------------------------------
        self.app_name = "SimplyUtd API"
        self.environment = os.getenv("ENVIRONMENT", "development").strip() or "development"
        self.debug = _bool("DEBUG", False)

        # --- Database (MongoDB) ------------------------------------------
        # "simplyutd" is the live DB. "simplyutd_test" is used by the test
        # suite (mongomock) so tests never touch real data.
        self.mongo_uri = os.getenv("MONGO_URI", "").strip() or "mongodb://localhost:27017"
        self.mongo_db = os.getenv("MONGO_DB", "").strip() or "simplyutd"
        # Local dev fallback: run against an in-memory mongomock database when
        # no MongoDB server is available. Data is not persisted across restarts
        # and this must never be enabled in production.
        self.use_mongomock = _bool("USE_MONGOMOCK", False)

        # --- Admin auth ---------------------------------------------------
        self.admin_api_key = os.getenv("ADMIN_API_KEY", "").strip()
        self.admin_key_header = "X-Admin-Key"

        # --- Email (Resend HTTPS API) ------------------------------------
        self.resend_api_key = os.getenv("RESEND_API_KEY", "").strip()
        self.resend_from = os.getenv(
            "RESEND_FROM", "SimplyUtd <onboarding@resend.dev>"
        ).strip()
        self.notify_to = os.getenv("NOTIFY_TO", "").strip()
        self.resend_api_url = "https://api.resend.com/emails"

        # --- Cloudinary ---------------------------------------------------
        self.cloudinary_cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME", "").strip()
        self.cloudinary_api_key = os.getenv("CLOUDINARY_API_KEY", "").strip()
        self.cloudinary_api_secret = os.getenv("CLOUDINARY_API_SECRET", "").strip()
        self.cloudinary_folder = os.getenv("CLOUDINARY_FOLDER", "simplyutd").strip()

        # --- RSS ingestion ------------------------------------------------
        self.rss_feeds = _list(
            "RSS_FEEDS",
            "https://feeds.bbci.co.uk/sport/football/teams/manchester-united/rss.xml,"
            "https://www.theguardian.com/football/manchesterunited/rss,"
            "https://www.skysports.com/rss/11095,"
            "https://www.manchestereveningnews.co.uk/all-about/manchester-united-fc?service=rss,"
            "https://www.90min.com/posts.rss,"
            "https://www.espn.co.uk/espn/rss/football/news",
        )
        self.rss_user_agent = os.getenv(
            "RSS_USER_AGENT",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        ).strip()
        self.ingest_enabled = _bool("INGEST_ENABLED", True)
        # The front page is refreshed every ten minutes.
        self.ingest_interval_minutes = _int("INGEST_INTERVAL_MINUTES", 10)
        # Mirror RSS images into Cloudinary (when configured) so hot-links to
        # third-party CDNs are avoided. Falls back to the remote URL.
        self.ingest_mirror_images = _bool("INGEST_MIRROR_IMAGES", True)
        self.news_max_per_feed = _int("NEWS_MAX_PER_FEED", 20)
        # Some feeds publish only a title and link. For those we read the
        # publisher's OpenGraph link-preview metadata (short description + image)
        # so cards still have text — never the article body itself.
        self.news_fetch_excerpts = _bool("NEWS_FETCH_EXCERPTS", True)
        self.news_excerpt_max = _int("NEWS_EXCERPT_MAX", 40)
        self.news_excerpt_min_summary = _int("NEWS_EXCERPT_MIN_SUMMARY", 160)
        self.news_excerpt_length = _int("NEWS_EXCERPT_LENGTH", 600)
        self.news_excerpt_timeout = float(_int("NEWS_EXCERPT_TIMEOUT", 8))

        # --- Hub ----------------------------------------------------------
        # The hub is derived from the ingested RSS news (results, fixtures,
        # overview) and from Wikipedia: the club's season records (squad,
        # appearances, goals) and the real Premier League table. Both are cached
        # to avoid hammering the upstream sources on every request.
        self.hub_wikipedia_enabled = _bool("HUB_WIKIPEDIA_ENABLED", True)
        self.hub_squad_page = os.getenv(
            "HUB_SQUAD_PAGE", "Manchester United F.C."
        ).strip()
        self.hub_season_page = os.getenv(
            "HUB_SEASON_PAGE", "2026–27 Manchester United F.C. season"
        ).strip()
        self.hub_table_page = os.getenv(
            "HUB_TABLE_PAGE", "2026–27 Premier League"
        ).strip()
        self.hub_cache_seconds = _int("HUB_CACHE_SECONDS", 21600)
        # The standings are what fans notice going stale, so they get their own
        # much shorter window and a background refresher (see main.py) keeps the
        # cache warm - a request almost never pays for the upstream fetch.
        self.hub_standings_cache_seconds = _int("HUB_STANDINGS_CACHE_SECONDS", 600)
        self.hub_refresh_seconds = _int("HUB_REFRESH_SECONDS", 600)
        self.hub_http_timeout = float(_int("HUB_HTTP_TIMEOUT", 12))
        self.hub_user_agent = os.getenv(
            "HUB_USER_AGENT", "SimplyUtd/1.0 (+https://simplyutd.com)"
        ).strip()

        # --- CORS ---------------------------------------------------------
        self.frontend_origins = _list(
            "FRONTEND_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173,"
            "https://simplyutd.com,https://www.simplyutd.com",
        )

    @property
    def cloudinary_configured(self) -> bool:
        return bool(
            self.cloudinary_cloud_name
            and self.cloudinary_api_key
            and self.cloudinary_api_secret
        )

    @property
    def email_configured(self) -> bool:
        return bool(self.resend_api_key and self.resend_from)


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()


settings = get_settings()
