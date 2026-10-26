"""``GET /api/v1/runner/contract`` (feature 018, FR-018.34): the runner contract as this deployment
serves it - the published file's values and the settings in force. Read by
``dataworks_get_runner_contract``; no database, no call to the runner."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ....services.code_rules import views

router = APIRouter(prefix="/api/v1/runner", tags=["code_rules"])


@router.get("/contract")
async def get_runner_contract() -> dict[str, Any]:
    """Language, allowed modules, the floor and the limits in force, the count cap, the reasons,
    the diagnostic length, the dry-run limits, the caps and whether code rules are switched on."""
    return views.runner_contract()
