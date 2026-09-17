"""The @SimplyUtd X timeline behind the home page's "Live United" panel.

The panel shows what @SimplyUtd posts on X, read through Nitter RSS mirrors (see
:mod:`app.services.x_service`). It is a read-only public endpoint: the panel
always degrades to an empty state rather than an error when every mirror is
unreachable.
"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from ..services import x_service

router = APIRouter(prefix="/api/x", tags=["x"])


@router.get("/posts")
def list_x_posts(
    response: Response,
    background: BackgroundTasks,
    limit: int = Query(20, ge=1, le=50),
) -> dict:
    """@SimplyUtd's most recent posts, newest first, pinned post leading.

    The panel polls this while it is on screen, so a cached read is refreshed
    just after the response goes out once it has gone stale: a new post reaches
    the panel within a poll or two without any reader waiting on a mirror.
    """
    payload = x_service.timeline(limit=limit)
    if x_service.revalidate_due():
        background.add_task(x_service.revalidate)
    # The panel must never read a cached body: freshness is decided here.
    response.headers["cache-control"] = "no-store"
    return payload


@router.get("/media/{post_id}")
def get_x_media(post_id: str) -> dict:
    """Playable video URLs for one post, resolved when a reader hits play."""
    return x_service.fetch_media(post_id)


@router.get("/media/{post_id}/stream")
def stream_x_media(post_id: str, request: Request) -> StreamingResponse:
    """Relay a post's video bytes, because the CDN rejects a browser's Referer.

    Range requests are forwarded untouched, so the player keeps seeking and
    browsers can still start playback before the whole clip has downloaded.
    """
    source = x_service.video_source(post_id)
    if not source:
        raise HTTPException(status_code=404, detail="No playable video for that post.")

    upstream = x_service.open_video_stream(source, request.headers.get("range"))
    if upstream is None:
        raise HTTPException(status_code=502, detail="Video source is unavailable.")

    return StreamingResponse(
        upstream.chunks,
        status_code=upstream.status_code,
        headers=upstream.headers,
        background=BackgroundTask(upstream.close),
    )
