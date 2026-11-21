"""Purge: permanent delete of archived items, operator only (operator decision 2026-10-20).

Every route refuses an agent-originated request (``403 agent_forbidden``) before reading its body,
and none has an MCP tool (``tests/support/mcp_ledger.py``, ``ROUTE_EXEMPT``). The rules live in
``services/purge_service.py``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.agent_origin import Actor, refusing_agents, resolve_who
from ....core.database import get_db
from ....schemas.purge import (
    PurgeBatchOut,
    PurgeBatchRequest,
    PurgeKind,
    PurgeOutcome,
    PurgePlanRequest,
    PurgeRequest,
)
from ....services import purge_service

router = APIRouter(prefix="/api/v1/purge", tags=["purge"])

operator_only = refusing_agents(
    "Permanent delete is operator-only: agents can archive, but only the operator deletes, on "
    "the archived view of the screen."
)


@router.post("/plan", response_model=PurgeBatchOut)
async def plan_purge(
    body: PurgePlanRequest,
    db: AsyncSession = Depends(get_db),
    _actor: Actor = Depends(operator_only),
) -> PurgeBatchOut:
    """What a purge of each item would remove, or why it is refused. Deletes nothing."""
    outcomes = await purge_service.plan_many(db, [(i.kind, i.id) for i in body.items])
    return _batch(outcomes)


@router.post("", response_model=PurgeBatchOut)
async def purge_items(
    body: PurgeBatchRequest,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(operator_only),
) -> PurgeBatchOut:
    """Purge each archived item on its own; a refused item never stops the others."""
    who = await resolve_who(actor, db)
    outcomes = await purge_service.purge_many(
        db, who, [(i.kind, i.id) for i in body.items], body.reason
    )
    return _batch(outcomes)


@router.post("/{kind}/{entity_id}", response_model=PurgeOutcome)
async def purge_one(
    kind: PurgeKind,
    entity_id: str,
    body: PurgeRequest | None = None,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(operator_only),
) -> PurgeOutcome:
    """Purge one archived item; ``409 not_archived`` or ``409 purge_blocked`` naming each
    blocker."""
    who = await resolve_who(actor, db)
    outcome = await purge_service.purge(db, who, kind, entity_id, body.reason if body else None)
    return PurgeOutcome.model_validate(outcome.out())


def _batch(outcomes: list[purge_service.Outcome]) -> PurgeBatchOut:
    items = [PurgeOutcome.model_validate(o.out()) for o in outcomes]
    return PurgeBatchOut(
        items=items,
        purged=sum(1 for o in outcomes if o.status == "purged"),
        blocked=sum(1 for o in outcomes if o.status == "blocked"),
        bytes=sum(o.bytes for o in outcomes if o.status != "blocked"),
    )
