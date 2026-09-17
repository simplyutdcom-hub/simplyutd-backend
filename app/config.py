"""Application configuration loaded from environment variables.

All settings are read once at import time. ``backend/.env`` is loaded if present
so the service works locally without exporting variables manually.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import httpx
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
        # Stories already stored without a thumbnail are re-checked a few at a
        # time, so an old corpus heals itself without hammering publishers.
        self.news_backfill_max = _int("NEWS_BACKFILL_MAX", 12)
        self.news_image_attempts = _int("NEWS_IMAGE_ATTEMPTS", 5)
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
        # The table's "Last 5" column needs each club's results in the order they
        # were played, which Wikipedia's results grid does not carry. ESPN's
        # per-club schedules do, so they are read in parallel and cached for much
        # longer than the table itself - one refresh is 20 requests, and only the
        # background refresher pays for it.
        self.hub_form_enabled = _bool("HUB_FORM_ENABLED", True)
        self.hub_form_league = os.getenv("HUB_FORM_LEAGUE", "eng.1").strip() or "eng.1"
        self.hub_form_cache_seconds = _int("HUB_FORM_CACHE_SECONDS", 21600)
        self.hub_form_workers = _int("HUB_FORM_WORKERS", 6)

        # --- X feed (the Live United panel) -------------------------------
        # The panel mirrors @SimplyUtd's X timeline. X's own embeds and public
        # syndication endpoints need a paid key or rate limit the server, so the
        # timeline is read from Nitter RSS mirrors instead: unofficial, so a
        # pool of instances is tried in order and the read is cached.
        self.x_handle = os.getenv("X_HANDLE", "SimplyUtd").strip().lstrip("@") or "SimplyUtd"
        self.x_rss_instances = _list(
            "X_RSS_INSTANCES",
            "https://nitter.kareem.one,https://nitter.thepixora.com,https://twiiit.com",
        )
        # Short: the panel is meant to read as "live" and Nitter is cheap to poll.
        self.x_cache_seconds = _int("X_CACHE_SECONDS", 180)
        # The panel polls for new posts, so a poll that finds a read older than
        # this is answered from the cache while the read is refreshed off-thread.
        # It caps how often a mirror is asked, however many readers are on the
        # page, and keeps a new post about a minute away from the panel.
        self.x_revalidate_seconds = _int("X_REVALIDATE_SECONDS", 30)
        # The account card barely changes, so it is cached separately.
        self.x_profile_cache_seconds = _int("X_PROFILE_CACHE_SECONDS", 3600)
        self.x_http_timeout = float(_int("X_HTTP_TIMEOUT", 12))
        self.x_max_posts = _int("X_MAX_POSTS", 20)
        self.x_user_agent = os.getenv(
            "X_USER_AGENT",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        ).strip()

        # --- Live United match (the score card in the X panel) -------------
        # Wikipedia, the Hub's source, carries dates but neither kickoff times
        # nor scores, so the card reads ESPN's public soccer JSON instead. It is
        # key-less and polled, so the read is cached against the panel's rate.
        self.live_score_enabled = _bool("LIVE_SCORE_ENABLED", True)
        self.live_score_base_url = os.getenv(
            "LIVE_SCORE_BASE_URL",
            "https://site.api.espn.com/apis/site/v2/sports/soccer",
        ).strip().rstrip("/")
        self.live_score_team_id = os.getenv("LIVE_SCORE_TEAM_ID", "360").strip() or "360"
        # United can turn up in any competition they enter, so every league is
        # queried and the match inside the window wins.
        self.live_score_leagues = _list(
            "LIVE_SCORE_LEAGUES",
            "eng.1,eng.fa,eng.league_cup,uefa.champions,uefa.europa",
        )
        # Short: a match in progress is exactly what the card is for.
        self.live_score_cache_seconds = _int("LIVE_SCORE_CACHE_SECONDS", 60)
        # A match runs ~135 minutes with half-time and stoppage, and the card
        # stays up for two hours past the final whistle.
        self.live_score_match_minutes = _int("LIVE_SCORE_MATCH_MINUTES", 135)
        self.live_score_grace_minutes = _int("LIVE_SCORE_GRACE_MINUTES", 120)
        self.live_score_timeout = float(_int("LIVE_SCORE_TIMEOUT", 12))
        # ESPN's CDN blocks browser-shaped agents (and requests that send no
        # agent at all) but lets library defaults through, so the default here
        # mirrors httpx's own agent rather than a browser string.
        self.live_score_user_agent = os.getenv(
            "LIVE_SCORE_USER_AGENT", f"python-httpx/{httpx.__version__}"
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
