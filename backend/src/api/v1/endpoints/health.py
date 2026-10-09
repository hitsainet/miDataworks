"""``GET /api/health`` (ADR-013; Foundation task 3.7). Mounted at ``/api``, not ``/api/v1``."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ....services.health_service import health

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def get_health() -> dict[str, Any]:
    """Each dependency's state. Always 200 while the API is up; a down dependency is reported."""
    return await health()
