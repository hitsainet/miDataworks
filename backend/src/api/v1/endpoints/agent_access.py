"""``GET /api/v1/agent-access``: the data for the display-only Agent access card (010 FTID 5.6).

Nothing here is editable (P-08): tool categories, the approval threshold and the window are
deployment configuration. The MCP server's categories come from its own ``/health`` (2 s timeout);
when it cannot be read the card says so with a reason rather than showing a guess.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.agent_origin import APPROVAL_ACTIONS
from ....core.config import get_settings
from ....core.database import get_db
from ....services.agent_activity_service import activity_summary

router = APIRouter(prefix="/api/v1/agent-access", tags=["agent-access"])

MCP_HEALTH_TIMEOUT_S = 2.0


async def read_mcp_health(internal_url: str | None) -> dict[str, Any]:
    """``{"reachable", "categories", "reason"}`` from the MCP server's ``/health``."""
    if not internal_url:
        return {"reachable": False, "categories": None, "reason": "MCP_INTERNAL_URL is not set"}
    url = internal_url.rstrip("/") + "/health"
    try:
        async with httpx.AsyncClient(timeout=MCP_HEALTH_TIMEOUT_S) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        return {
            "reachable": False,
            "categories": None,
            "reason": f"unreachable ({type(exc).__name__})",
        }
    if not response.is_success:
        return {
            "reachable": False,
            "categories": None,
            "reason": f"answered {response.status_code}",
        }
    try:
        body = response.json()
    except ValueError:
        return {"reachable": False, "categories": None, "reason": "answered without JSON"}
    categories = body.get("categories") if isinstance(body, dict) else None
    if not isinstance(categories, list):
        return {"reachable": False, "categories": None, "reason": "no categories in /health"}
    return {
        "reachable": True,
        "categories": sorted(str(c) for c in categories),
        "reason": None,
        "unknown_categories": body.get("unknown_categories") or [],
    }


@router.get("")
async def get_agent_access(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    settings = get_settings()
    return {
        "mcp_public_url": settings.mcp_public_url,
        "mcp": await read_mcp_health(settings.mcp_internal_url),
        "gated_actions": dict(APPROVAL_ACTIONS),
        "label_threshold": settings.agent_label_row_threshold,
        "label_window_hours": settings.agent_label_window_hours,
        "approval_ttl_hours": settings.approval_ttl_hours,
        "activity": await activity_summary(db),
    }
