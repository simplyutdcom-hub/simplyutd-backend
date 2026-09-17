"""FastAPI application entrypoint for the SimplyUtd backend."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__, db as db_module
from .config import settings
from .routers import (
    admin,
    analytics,
    auth,
    comments,
    contact,
    health,
    home,
    hub,
    live,
    news,
    newsletter,
    search,
    store,
    x,
)
from .services.ingest import scheduler
from .services.seed import seed_all
from .services import hub_service

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("simplyutd")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Connect to Mongo, seed defaults and start the RSS ingestion loop."""
    database = db_module.connect()
    try:
        seed_all(database)
    except Exception:  # noqa: BLE001 - seeding must never block startup
        logger.exception("Seed failed")

    ingest_task: asyncio.Task | None = None
    if settings.ingest_enabled:
        ingest_task = asyncio.create_task(scheduler(database), name="rss-ingest")
    else:
        logger.info("RSS ingestion disabled")

    # Keeps the hub's league table current without making a request wait on
    # Wikipedia - see hub_service.hub_refresher.
    hub_task = asyncio.create_task(hub_service.hub_refresher(), name="hub-refresh")

    try:
        yield
    finally:
        for task in (ingest_task, hub_task):
            if task is None:
                continue
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        db_module.close()


app = FastAPI(
    title=settings.app_name,
    version=__version__,
    description=(
        "Backend for SimplyUtd — Manchester United news, hub data, store, "
        "contact and admin, powered by MongoDB, Cloudinary and RSS ingestion."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.frontend_origins,
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error."})


for module in (
    health,
    home,
    news,
    live,
    hub,
    store,
    contact,
    newsletter,
    analytics,
    comments,
    auth,
    search,
    admin,
    x,
):
    app.include_router(module.router)


@app.get("/", tags=["system"])
def root() -> dict:
    return {
        "name": settings.app_name,
        "version": __version__,
        "docs": "/docs",
        "health": "/api/health",
    }
