"""Behaviour routes (feature 017): a read of other objects' keys, never a registry (P-25).

``GET /api/v1/behaviours/{key}/resolve`` (FR-017.10) reads miStudio's catalogue at run time; with
miStudio absent the answer is "not verified against a catalogue", never an error, so every agent
step works standalone (FR-017.62). Not gated: it writes nothing and loads nothing.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path
from fastapi.concurrency import run_in_threadpool

from ....schemas.behaviour import BEHAVIOUR_KEY_PATTERN
from ....schemas.behaviour_datasets import ResolveOut
from ....services.behaviour import resolve_service

router = APIRouter(prefix="/api/v1/behaviours", tags=["behaviours"])


@router.get("/{key}/resolve", response_model=ResolveOut)
async def resolve_behaviour(
    key: str = Path(..., pattern=BEHAVIOUR_KEY_PATTERN),
) -> dict[str, Any]:
    """The behaviour's definition as miStudio's catalogue holds it now, or why it is not
    verified. ``record`` is what a build records (pass it to ``carrier_plan@1.definition``)."""
    return await run_in_threadpool(resolve_service.resolve, key)
