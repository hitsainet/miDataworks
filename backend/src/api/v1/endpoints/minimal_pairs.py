"""Minimal-pair chains (009 FR-009.60 - FR-009.64; operator decision 2026-10-07).

``POST /minimal-pair-chains`` plans first — every refusal before anything runs — then starts the
chain's first stage (a 007 generation run) and returns ``202`` with the chain, each stage's run,
job and version ID as they appear. The chain advances itself (Beat); ``resume`` continues the stage
that stopped and ``cancel`` stops the live one through its own owner.

No route takes a secret, loads a model or adds an approval of its own: the generation run is
ungated for agents (FR-007.30) and a judge run an agent's chain would start over the P-07 threshold
stops the chain at ``judge`` for an operator to resume.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.agent_origin import Actor, get_actor, resolve_who
from ....core.database import get_db
from ....schemas.minimal_pairs import (
    MinimalPairChainCreate,
    MinimalPairChainList,
    MinimalPairChainOut,
    MinimalPairChainPlan,
)
from ....services.detector_sets import minimal_pair_chain as svc
from ._paging import Page, paging

router = APIRouter(prefix="/api/v1", tags=["minimal-pairs"])


@router.post("/minimal-pair-chains/plan", response_model=MinimalPairChainPlan)
async def plan_chain(
    body: MinimalPairChainCreate,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> MinimalPairChainPlan:
    """A dry run: every refusal, the generation plan and the judge; writes nothing."""
    return (await svc.plan(db, body, actor.origin)).out


@router.post(
    "/minimal-pair-chains",
    response_model=MinimalPairChainOut,
    status_code=202,
    responses={202: {"description": "Stage one (the generation run) is queued"}},
)
async def start_chain(
    body: MinimalPairChainCreate,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> JSONResponse:
    who = await resolve_who(actor, db)  # NO_IDENTITY
    planned = await svc.plan(db, body, who.origin)
    chain = await svc.start(db, body, planned, who)
    out = await svc.chain_out(db, await svc.advance(db, chain.id))
    return JSONResponse(status_code=202, content=out.model_dump(mode="json"))


@router.get("/minimal-pair-chains", response_model=MinimalPairChainList)
async def list_chains(
    state: str | None = Query(None, pattern="^(running|completed|failed|cancelled)$"),
    input_version_id: str | None = Query(None, max_length=64),
    page: Page = Depends(paging),
    db: AsyncSession = Depends(get_db),
) -> MinimalPairChainList:
    rows, total = await svc.list_chains(
        db, state=state, input_version_id=input_version_id, page=page.page, limit=page.limit
    )
    return MinimalPairChainList(
        items=[await svc.chain_out(db, r) for r in rows],
        total=total,
        page=page.page,
        limit=page.limit,
    )


@router.get("/minimal-pair-chains/{chain_id}", response_model=MinimalPairChainOut)
async def get_chain(chain_id: str, db: AsyncSession = Depends(get_db)) -> MinimalPairChainOut:
    return await svc.chain_out(db, await svc.get_chain(db, chain_id))


@router.post("/minimal-pair-chains/{chain_id}/resume", response_model=MinimalPairChainOut)
async def resume_chain(
    chain_id: str, db: AsyncSession = Depends(get_db), actor: Actor = Depends(get_actor)
) -> MinimalPairChainOut:
    who = await resolve_who(actor, db)
    return await svc.chain_out(db, await svc.resume(db, chain_id, who))


@router.post("/minimal-pair-chains/{chain_id}/cancel", response_model=MinimalPairChainOut)
async def cancel_chain(chain_id: str, db: AsyncSession = Depends(get_db)) -> MinimalPairChainOut:
    return await svc.chain_out(db, await svc.cancel(db, chain_id))
