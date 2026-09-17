"""Admin API — all routes require the ``X-Admin-Key`` header."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ...security import require_admin
from . import hub, messages, news, news_sources, products, stats, subscribers, uploads

router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)], tags=["admin"])

# Registered before ``news`` so ``/news/sources`` wins over ``/news/{news_id}``.
router.include_router(news_sources.router)
router.include_router(news.router)
router.include_router(products.router)
router.include_router(messages.router)
router.include_router(subscribers.router)
router.include_router(stats.router)
router.include_router(uploads.router)
router.include_router(hub.router)

__all__ = ["router"]
