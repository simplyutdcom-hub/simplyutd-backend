"""Comments REST endpoints plus the live comment WebSocket.

The REST API is the source of truth (create / like / delete / list); every
mutation is also broadcast over the room WebSocket so open pages update without
polling. The socket accepts the same operations as messages, which is what the
"chat" feel of a comment thread needs.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from pymongo.database import Database

from ..db import get_db
from ..schemas import CommentCreate, CommentLike, CommentThreadOut
from ..config import settings
from ..security import is_admin_key
from ..services import comment_service
from ..services.comment_hub import hub, room_key, rooms_for

logger = logging.getLogger("simplyutd.comments")

router = APIRouter(prefix="/api/comments", tags=["comments"])

Target = Query(..., pattern="^(story|live)$")
Sort = Query("newest", pattern="^(newest|oldest|top)$")


def _fail(exc: comment_service.CommentError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.detail)


async def _broadcast_thread(target_id: str, message: dict[str, Any]) -> None:
    """A thread is shared by the story and live surfaces, so notify both."""
    if not target_id:
        return
    for room in rooms_for(target_id):
        await hub.broadcast(room, message)


@router.get("", response_model=CommentThreadOut)
def list_comments(
    target_type: str = Target,
    target_id: str = Query(..., min_length=1, max_length=120),
    sort: str = Sort,
    limit: int = Query(200, ge=1, le=comment_service.MAX_LIMIT),
    visitor_id: str | None = Query(None, max_length=80),
    database: Database = Depends(get_db),
) -> CommentThreadOut:
    try:
        thread = comment_service.list_thread(
            database, target_type, target_id, sort=sort, limit=limit, viewer_id=visitor_id
        )
    except comment_service.CommentError as exc:
        raise _fail(exc) from exc
    return CommentThreadOut(**thread)


@router.post("", status_code=201)
async def create_comment(
    payload: CommentCreate, database: Database = Depends(get_db)
) -> dict[str, Any]:
    try:
        comment, total = comment_service.create(
            database,
            target_type=payload.target_type,
            target_id=payload.target_id,
            body=payload.body,
            author=payload.author,
            parent_id=payload.parent_id,
            visitor_id=payload.visitor_id,
        )
    except comment_service.CommentError as exc:
        raise _fail(exc) from exc

    await _broadcast_thread(
        payload.target_id, {"type": "comment", "comment": comment, "total": total}
    )
    return {"ok": True, "comment": comment, "total": total}


@router.get("/stats", response_model=dict)
def comment_stats(database: Database = Depends(get_db)) -> dict[str, Any]:
    return comment_service.stats(database)


@router.get("/count", response_model=dict)
def comment_count(
    target_type: str = Target,
    target_id: str = Query(..., min_length=1, max_length=120),
    database: Database = Depends(get_db),
) -> dict[str, Any]:
    total = comment_service.count(database, target_id)
    return {"ok": True, "target_type": target_type, "target_id": target_id, "total": total}


@router.get("/recent", response_model=dict)
def recent_comments(
    target_type: str = Target,
    ids: str = Query("", description="Comma separated article ids"),
    per_article: int = Query(1, ge=1, le=5),
    database: Database = Depends(get_db),
) -> dict[str, Any]:
    wanted = [value.strip() for value in ids.split(",") if value.strip()][:50]
    grouped = comment_service.recent_for_articles(
        database, target_type, wanted, per_article=per_article
    )
    return {"ok": True, "items": grouped, "total": len(wanted)}


@router.post("/{comment_id}/like", response_model=dict)
async def like_comment(
    comment_id: str, payload: CommentLike, database: Database = Depends(get_db)
) -> dict[str, Any]:
    try:
        comment = comment_service.toggle_like(database, comment_id, payload.visitor_id)
    except comment_service.CommentError as exc:
        raise _fail(exc) from exc
    await _broadcast_thread(
        comment.get("target_id") or "",
        {
            "type": "like",
            "comment_id": comment["id"],
            "likes": comment["likes"],
            "visitor_id": payload.visitor_id,
        },
    )
    return {"ok": True, "comment": comment}


@router.delete("/{comment_id}", response_model=dict)
async def delete_comment(
    comment_id: str,
    visitor_id: str | None = Query(None, max_length=80),
    admin_key: str | None = Query(None),
    x_admin_key: str | None = Header(default=None, alias=settings.admin_key_header),
    database: Database = Depends(get_db),
) -> dict[str, Any]:
    try:
        target_id, total = comment_service.remove(
            database,
            comment_id,
            visitor_id=visitor_id,
            is_admin=is_admin_key(admin_key) or is_admin_key(x_admin_key),
        )
    except comment_service.CommentError as exc:
        raise _fail(exc) from exc
    await _broadcast_thread(
        target_id, {"type": "deleted", "comment_id": comment_id, "total": total}
    )
    return {"ok": True, "id": comment_id, "total": total, "target_id": target_id}


# --- WebSocket -------------------------------------------------------------

def _socket_author(intro: dict[str, Any]) -> str | None:
    name = (intro.get("author") or "").strip()
    return name[: comment_service.MAX_AUTHOR] if name else None


async def _send_thread(
    websocket: WebSocket,
    database: Database,
    target_type: str,
    target_id: str,
    room: str,
    visitor_id: str | None = None,
) -> None:
    thread = comment_service.list_thread(
        database, target_type, target_id, sort="newest", viewer_id=visitor_id
    )
    await websocket.send_json(
        {
            "type": "welcome",
            "items": thread["items"],
            "total": thread["total"],
            "online": hub.online(room),
            "room": room,
        }
    )


async def _handle_socket_message(
    websocket: WebSocket,
    database: Database,
    room: str,
    target_type: str,
    target_id: str,
    meta: dict[str, Any],
    message: dict[str, Any],
) -> None:
    kind = str(message.get("type") or "").lower()

    if kind in ("ping", "heartbeat"):
        await websocket.send_json(
            {"type": "pong", "t": message.get("t"), "online": hub.online(room)}
        )
        return

    if kind == "typing":
        await hub.broadcast(
            room,
            {
                "type": "typing",
                "author": meta.get("author") or comment_service.DEFAULT_AUTHOR,
                "visitor_id": meta.get("visitor_id"),
                "typing": bool(message.get("typing", True)),
            },
        )
        return

    if kind == "like":
        try:
            comment = comment_service.toggle_like(
                database, str(message.get("comment_id") or ""), str(meta.get("visitor_id") or "")
            )
        except comment_service.CommentError as exc:
            await websocket.send_json({"type": "error", "detail": exc.detail, "op": "like"})
            return
        await hub.broadcast(
            room,
            {
                "type": "like",
                "comment_id": comment["id"],
                "likes": comment["likes"],
                "visitor_id": meta.get("visitor_id"),
            },
        )
        return

    if kind in ("message", "comment", "send"):
        body = str(message.get("body") or message.get("text") or "")
        try:
            comment, total = comment_service.create(
                database,
                target_type=target_type,
                target_id=target_id,
                body=body,
                author=meta.get("author"),
                parent_id=(message.get("parent_id") or None),
                visitor_id=meta.get("visitor_id"),
            )
        except comment_service.CommentError as exc:
            await websocket.send_json({"type": "error", "detail": exc.detail, "op": "create"})
            return
        await hub.broadcast(room, {"type": "comment", "comment": comment, "total": total})
        return

    if kind == "delete":
        try:
            _, total = comment_service.remove(
                database,
                str(message.get("comment_id") or ""),
                visitor_id=meta.get("visitor_id"),
                is_admin=False,
            )
        except comment_service.CommentError as exc:
            await websocket.send_json({"type": "error", "detail": exc.detail, "op": "delete"})
            return
        await hub.broadcast(
            room, {"type": "deleted", "comment_id": message.get("comment_id"), "total": total}
        )
        return

    if kind == "identity":
        meta["author"] = _socket_author(message) or meta.get("author")
        meta["visitor_id"] = (message.get("visitor_id") or meta.get("visitor_id")) or None
        return

    await websocket.send_json({"type": "error", "detail": f"Unknown message type '{kind}'"})


@router.websocket("/ws/{target_type}/{target_id}")
async def comments_socket(
    websocket: WebSocket, target_type: str, target_id: str, database: Database = Depends(get_db)
) -> None:
    await websocket.accept()
    try:
        target_type = comment_service.normalise_target(target_type)
    except comment_service.CommentError:
        await websocket.send_json(
            {"type": "error", "detail": f"Unsupported comment target '{target_type}'"}
        )
        await websocket.close(code=4404)
        return

    room = room_key(target_type, target_id)
    meta: dict[str, Any] = {"author": None, "visitor_id": None}
    await hub.register(room, websocket, author=None, visitor_id=None)
    try:
        await _send_thread(websocket, database, target_type, target_id, room)
        await hub.broadcast(room, {"type": "presence", "online": hub.online(room)})
        while True:
            raw = await websocket.receive_json()
            if not isinstance(raw, dict):
                await websocket.send_json({"type": "error", "detail": "Expected a JSON object"})
                continue
            if meta.get("author") is None and raw.get("author"):
                meta["author"] = _socket_author(raw)
            if meta.get("visitor_id") is None and raw.get("visitor_id"):
                meta["visitor_id"] = str(raw["visitor_id"])[:80]
            identity = str(raw.get("type") or "").lower() == "identity"
            await _handle_socket_message(
                websocket, database, room, target_type, target_id, meta, raw
            )
            if identity:
                # The welcome snapshot goes out before we know who is reading,
                # so tell the client which of those comments are theirs.
                await websocket.send_json(
                    {
                        "type": "viewer",
                        **comment_service.viewer_state(database, target_id, meta.get("visitor_id")),
                    }
                )
                await hub.broadcast(room, {"type": "presence", "online": hub.online(room)})
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 - never let a socket error take down the app
        logger.debug("Comment socket closed unexpectedly for %s", room, exc_info=True)
    finally:
        await hub.unregister(room, websocket)
        await hub.broadcast(room, {"type": "presence", "online": hub.online(room)})
