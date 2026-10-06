# Origin: miStudio (Onegaishimas/miStudio) backend/src/workers/websocket_emitter.py @ c829a2cc
# Mode: adapt (docs/REUSE.md). Kept: workers emit by HTTP POST to an internal route, never by
# holding a socket; a pooled httpx client; an emit failure is logged and never breaks the work.
# Dropped: the twenty per-domain helpers; one function takes a room name (ADR-008).
"""Worker-side emit through the API's internal route (ADR-008; Foundation task 6.2)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ..core.config import get_settings

logger = logging.getLogger(__name__)

INTERNAL_EMIT_PATH = "/internal/ws/emit"

_client: httpx.Client | None = None


def _http() -> httpx.Client:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.Client(timeout=5.0)
    return _client


def emit(room: str, event: str, data: dict[str, Any]) -> bool:
    """POST one event for one room. Returns False (and logs) on any failure.

    Progress over the socket is a convenience; the job row is the record. A failed emit must not
    stop the job, and the database heartbeat (core/cancellation.record_progress) is what keeps
    the janitor away, not this.
    """
    settings = get_settings()
    try:
        response = _http().post(
            settings.dataworks_api_url.rstrip("/") + INTERNAL_EMIT_PATH,
            json={"room": room, "event": event, "data": data},
            headers={"X-Internal-Token": settings.internal_api_secret.get_secret_value()},
        )
    except httpx.HTTPError as exc:
        logger.warning("Emit to %s failed: %s", room, exc)
        return False
    if response.status_code != 204:
        logger.warning("Emit to %s answered %s", room, response.status_code)
        return False
    return True
