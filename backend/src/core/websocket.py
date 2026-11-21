"""Socket.IO server and rooms (ADR-008; Foundation task 6.1).

Rooms are ``dataworks/<kind>/<id>`` (for example ``dataworks/label-runs/{id}``) plus the shared
``dataworks/approvals`` room. A client subscribes by name; an emit reaches only that room's
members. Workers never hold a socket: they POST to the internal emit route
(``api/internal.py``), which calls :func:`emit_to_room`.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import socketio

logger = logging.getLogger(__name__)

#: Mounted under the backend's ``/api/`` prefix so the ingress routes it to the backend.
SOCKETIO_PATH = "/api/ws/socket.io"

ROOM_PATTERN = re.compile(r"^dataworks/(?:[a-z0-9][a-z0-9-]*/[A-Za-z0-9_-]{1,64}|approvals|jobs)$")

sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins=[], logger=False)


def valid_room(room: Any) -> bool:
    return isinstance(room, str) and bool(ROOM_PATTERN.fullmatch(room))


async def subscribe(sid: str, data: Any) -> dict[str, Any]:
    room = data.get("room") if isinstance(data, dict) else data
    if not valid_room(room):
        return {"ok": False, "error": "INVALID_ROOM"}
    await sio.enter_room(sid, room)
    return {"ok": True, "room": room}


async def unsubscribe(sid: str, data: Any) -> dict[str, Any]:
    room = data.get("room") if isinstance(data, dict) else data
    if not valid_room(room):
        return {"ok": False, "error": "INVALID_ROOM"}
    await sio.leave_room(sid, room)
    return {"ok": True, "room": room}


sio.on("subscribe", subscribe)
sio.on("unsubscribe", unsubscribe)


async def emit_to_room(room: str, event: str, data: dict[str, Any]) -> None:
    """Broadcast one event to one room. The only emit path in the backend."""
    if not valid_room(room):
        raise ValueError(f"invalid room {room!r}")
    await sio.emit(event, {"room": room, **data}, room=room)
