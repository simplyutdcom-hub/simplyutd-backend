"""Room-based WebSocket fan-out used by the live comment chat.

One room per comment thread (``<target_type>:<target_id>``). The manager keeps
track of connected sockets so it can broadcast new comments, likes, presence and
typing indicators, and it prunes sockets that fail to send.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger("simplyutd.comments.ws")


def room_key(target_type: str, target_id: str) -> str:
    return f"{target_type}:{target_id}"


def rooms_for(target_id: str) -> tuple[str, str]:
    """Story and live surfaces of an article share one comment thread."""
    return room_key("story", target_id), room_key("live", target_id)


class CommentHub:
    """Tracks sockets per thread and fans messages out to them."""

    def __init__(self) -> None:
        self._rooms: dict[str, dict[WebSocket, dict[str, Any]]] = {}
        self._lock = asyncio.Lock()

    async def register(
        self, room: str, websocket: WebSocket, *, author: str | None = None, visitor_id: str | None = None
    ) -> None:
        async with self._lock:
            self._rooms.setdefault(room, {})[websocket] = {
                "author": author,
                "visitor_id": visitor_id,
            }

    async def unregister(self, room: str, websocket: WebSocket) -> None:
        async with self._lock:
            sockets = self._rooms.get(room)
            if sockets is None:
                return
            sockets.pop(websocket, None)
            if not sockets:
                self._rooms.pop(room, None)

    def online(self, room: str) -> int:
        return len(self._rooms.get(room, {}))

    def authors(self, room: str) -> list[str]:
        seen: list[str] = []
        for meta in self._rooms.get(room, {}).values():
            name = meta.get("author")
            if name and name not in seen and meta.get("visitor_id") != "__self__":
                seen.append(name)
        return seen

    async def broadcast(self, room: str, message: dict[str, Any]) -> None:
        """Send ``message`` to every socket in the room, pruning dead ones."""
        sockets = list(self._rooms.get(room, {}).keys())
        if not sockets:
            return
        results = await asyncio.gather(
            *(self._safe_send(socket, message) for socket in sockets), return_exceptions=True
        )
        dead = [socket for socket, ok in zip(sockets, results) if ok is not True]
        for socket in dead:
            await self.unregister(room, socket)

    async def _safe_send(self, websocket: WebSocket, message: dict[str, Any]) -> bool:
        try:
            await websocket.send_json(message)
            return True
        except Exception:  # noqa: BLE001 - a broken socket must not stop the broadcast
            logger.debug("Dropping websocket that failed to receive", exc_info=True)
            return False


hub = CommentHub()
