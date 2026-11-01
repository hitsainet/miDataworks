"""Rule derivations (feature 016; FR-016.29 - FR-016.32): start one, read one.

Not gated (FR-016.35): CPU work on local data. A start is validated BEFORE the job is queued, so a
missing or non-binary label column is refused at once, naming the column.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Path
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.agent_origin import Actor, get_actor, resolve_who
from ....core.database import get_db, get_sync_db
from ....schemas.rules import RuleDerivationIn
from ....services.job_service import dispatch_queued
from ....services.rules import derivation_service

router = APIRouter(prefix="/api/v1/rule-derivations", tags=["rule_signals"])


@router.post("", status_code=202)
async def start_derivation(
    body: RuleDerivationIn, db: AsyncSession = Depends(get_db), actor: Actor = Depends(get_actor)
) -> dict[str, Any]:
    """Derive a draft rule set from a labelled version and measure it on held-out rows only.
    Nothing is saved as a rule set until ``dataworks_save_rule_set`` saves the draft."""
    who = await resolve_who(actor, db)

    def run() -> dict[str, Any]:
        with get_sync_db() as session:
            out = derivation_service.start(session, body.model_dump(), who)
            dispatch_queued(session)
            return out

    return await run_in_threadpool(run)


@router.get("/{derivation_id}")
async def get_derivation(
    derivation_id: str = Path(..., pattern=r"^rdv_[0-9a-f]{24}$"),
) -> dict[str, Any]:
    """A derivation's state, figures (or "not measured"), draft and provenance."""

    def run() -> dict[str, Any]:
        with get_sync_db() as session:
            return derivation_service.out(session, derivation_id)

    return await run_in_threadpool(run)
