"""RSS ingestion orchestration and background scheduler."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from pymongo.database import Database

from .. import db as db_module
from ..config import settings
from ..utils import utcnow
from . import classify, cloudinary_service, feed_rank, news_sources
from .comment_service import seed_demo_comments
from .excerpt import backfill_images, enrich
from .news_service import prune_external, upsert_entry
from .rss import collect_entries

logger = logging.getLogger("simplyutd.ingest")

META_KEY = "rss_ingest"


def run_ingest(database: Database, *, mirror_images: bool | None = None) -> dict[str, Any]:
    """Fetch all feeds and persist new articles.

    Returns a summary dict with counts. Safe to call concurrently from the
    scheduler and from the admin trigger endpoint.
    """
    started = utcnow()
    mirror = settings.ingest_mirror_images if mirror_images is None else mirror_images
    mirror_available = mirror and cloudinary_service.is_configured()

    entries = collect_entries(feeds=news_sources.enabled_urls(database), include_google_news=False)
    try:
        enriched = enrich(entries)
    except Exception:  # noqa: BLE001 - previews are a nice-to-have
        logger.debug("Excerpt enrichment failed", exc_info=True)
        enriched = 0
    inserted = 0
    skipped = 0
    mirrored = 0
    mirror_attempts = 0

    for entry in entries:
        image_url = entry.image
        if mirror_available and image_url:
            mirror_attempts += 1
            stored = cloudinary_service.upload_remote(image_url, folder="news")
            if stored:
                image_url = stored
                mirrored += 1
        try:
            if upsert_entry(database, entry, image_url=image_url):
                inserted += 1
            else:
                skipped += 1
        except Exception:  # noqa: BLE001 - one bad entry must not stop ingestion
            logger.exception("Failed to store entry: %s", entry.link)
            skipped += 1

    pruned = 0
    try:
        pruned = prune_external(database)
    except Exception:  # noqa: BLE001
        logger.debug("Prune failed", exc_info=True)

    # Feed items that shipped no image (Google News never does) are re-checked
    # against their publisher's link preview, a few per run.
    backfilled = 0
    try:
        backfilled = backfill_images(database)
    except Exception:  # noqa: BLE001
        logger.debug("Image backfill failed", exc_info=True)

    # Keep the whole corpus scored: anything stored before the ranking algorithm
    # existed (or edited by hand since) gets its United verdict filled in here so
    # the feed never has to fall back to a plain date sort.
    scored = 0
    try:
        scored = feed_rank.backfill(database)
    except Exception:  # noqa: BLE001
        logger.debug("Ranking backfill failed", exc_info=True)

    # Articles ingested before the section classifier existed still carry the
    # generic label; filing them here keeps the "all stories" page meaningful
    # without the feed having to re-download anything.
    classified = 0
    try:
        classified = classify.backfill(database)
    except Exception:  # noqa: BLE001
        logger.debug("Category backfill failed", exc_info=True)

    summary = {
        "started_at": started,
        "finished_at": utcnow(),
        "interval_minutes": settings.ingest_interval_minutes,
        "sources": news_sources.count_enabled(database),
        "fetched": len(entries),
        "enriched": enriched,
        "backfilled_images": backfilled,
        "classified": classified,
        "inserted": inserted,
        "skipped": skipped,
        "mirrored_images": mirrored,
        "mirror_attempts": mirror_attempts,
        "mirror_error": cloudinary_service.last_error(),
        "pruned": pruned,
        "scored": scored,
        "status": "ok",
    }
    try:
        seed_demo_comments(database)
    except Exception:  # noqa: BLE001 - comments are decoration, never fatal
        logger.debug("Demo comment seeding after ingest failed", exc_info=True)

    try:
        database[db_module.META].update_one(
            {"key": META_KEY},
            {"$set": {**summary, "key": META_KEY}},
            upsert=True,
        )
    except Exception:  # noqa: BLE001
        logger.debug("Could not persist ingest status", exc_info=True)

    logger.info(
        "Ingest complete: fetched=%d inserted=%d skipped=%d",
        summary["fetched"],
        summary["inserted"],
        summary["skipped"],
    )
    return summary


def last_ingest(database: Database) -> dict[str, Any] | None:
    doc = database[db_module.META].find_one({"key": META_KEY}, {"_id": 0})
    return doc


async def scheduler(database: Database) -> None:
    """Run ingestion on startup then every ``INGEST_INTERVAL_MINUTES``."""
    if not settings.ingest_enabled:
        logger.info("RSS ingestion is disabled (INGEST_ENABLED=false)")
        return

    interval = max(1, settings.ingest_interval_minutes) * 60
    # Small delay so startup (seed + index creation) settles first.
    await asyncio.sleep(3)
    while True:
        try:
            await asyncio.to_thread(run_ingest, database)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("Scheduled ingestion failed")
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
