"""The internal emit route (ADR-008; Foundation tasks 6.2, 6.3).

Mounted at ``/internal/ws/emit`` — deliberately OUTSIDE ``/api/``. The ingress routes ``/api/``
to the backend and everything else to the frontend, so this path is unreachable from outside the
cluster by construction; ``tests/unit/test_internal_route_not_exposed.py`` pins that. It also
requires the shared ``X-Internal-Token``.
"""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Header, Response
from pydantic import BaseModel, Field

from ..core.config import get_settings
from ..core.errors import AppError
from ..core.websocket import emit_to_room, valid_room

router = APIRouter(prefix="/internal", tags=["internal"], include_in_schema=False)

INTERNAL_PREFIX = "/internal"


class EmitRequest(BaseModel):
    room: str = Field(..., max_length=200)
    event: str = Field(..., pattern=r"^[a-z_]+:[a-z_]+$", max_length=64)
    data: dict[str, Any] = Field(default_factory=dict)


@router.post("/ws/emit", status_code=204)
async def internal_emit(
    body: EmitRequest, x_internal_token: str | None = Header(None, alias="X-Internal-Token")
) -> Response:
    expected = get_settings().internal_api_secret.get_secret_value()
    if x_internal_token is None or not hmac.compare_digest(x_internal_token, expected):
        raise AppError("Unknown caller.", code="FORBIDDEN", status_code=403)
    if not valid_room(body.room):
        raise AppError("Invalid room.", code="INVALID_ROOM", status_code=422)
    await emit_to_room(body.room, body.event, body.data)
    return Response(status_code=204)
