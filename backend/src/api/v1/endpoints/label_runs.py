"""Label-run routes (FR-005.21, FR-005.26, FR-005.30 – FR-005.36, FR-005.45, FR-005.53, 005.54).

``POST /api/v1/label-runs`` plans first (preflight checks included, FR-005.54), then the approval
gate: an AGENT start whose P-07 sum is over the threshold is stored and answered ``202`` (C6). On
approval the stored body runs once; the plan is recomputed and a start that would score more rows
than were approved is refused ``PLAN_CHANGED``. The gate's predicate and the route read the SAME
plan, and the route refuses an un-approved agent start whose plan now needs approval
(``APPROVAL_REQUIRED``) rather than running past the gate.

Importing this module registers feature 005's ``label_run`` binding resolver with feature 002.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.agent_origin import Actor, get_actor, requires_approval_when_agent, resolve_who
from ....core.database import get_db
from ....core.errors import AppError, ConflictError, UnprocessableError
from ....schemas.labeling import (
    AggregateRequest,
    LabelPage,
    LabelRunList,
    LabelRunOut,
    LabelRunStart,
    Plan,
    RederiveRequest,
)
from ....services import agent_label_ledger
from ....services import label_run_service as svc
from ....services.labeling_ports import register_bindings
from ._paging import Page, paging

router = APIRouter(prefix="/api/v1/label-runs", tags=["labeling"])

register_bindings()


async def _needs_approval(values: dict[str, Any], db: AsyncSession) -> bool:
    """The gate's predicate (P-07). A request the plan REFUSES is not gated: the route then
    refuses it the same way, and no approval is created for a run that could never start
    (FR-005.54). Anything else that raises fails CLOSED in the decorator."""
    body = values["body"]
    assert isinstance(body, LabelRunStart)
    try:
        result = await svc.plan(db, body, "agent")
    except AppError:
        return False
    return result.plan.approval_needed


async def _approval_facts(values: dict[str, Any], db: AsyncSession) -> dict[str, Any]:
    """The plan the operator approves (shown on the card, bound by the digest)."""
    body = values["body"]
    assert isinstance(body, LabelRunStart)
    result = await svc.plan(db, body, "agent")
    return {"plan": result.plan.model_dump(mode="json")}


def _summary(payload: dict[str, Any]) -> str:
    plan = payload.get("plan") or {}
    return (
        f"Label {plan.get('rows_to_score', '?')} rows of version "
        f"{(payload.get('body') or {}).get('input_version_id', '?')} "
        f"({plan.get('agent_window_rows', 0)} agent rows already in the last 24 h)"
    )


@router.get("/plan", response_model=Plan)
async def plan_label_run(
    request: str = Query(..., max_length=20_000, description="The start body, as JSON"),
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> Plan:
    """A dry run of ``POST /label-runs``: rows, reuse, and whether approval will be needed."""
    try:
        body = LabelRunStart.model_validate(json.loads(request))
    except (ValueError, ValidationError) as exc:
        raise UnprocessableError(
            "The plan request is not a valid start body.",
            code="VALIDATION_ERROR",
            details={"error": str(exc)[:500]},
        ) from None
    return (await svc.plan(db, body, actor.origin)).plan


@router.post(
    "",
    response_model=LabelRunOut,
    status_code=201,
    responses={202: {"description": "Waiting for the operator's approval"}},
)
@requires_approval_when_agent(
    "agent_label_rows", when=_needs_approval, enrich=_approval_facts, summary=_summary
)
async def start_label_run(
    body: LabelRunStart,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> Any:
    who = await resolve_who(actor, db)
    if who.origin == "agent":
        await agent_label_ledger.lock_scope(db, body.input_version_id)
    result = await svc.plan(db, body, who.origin)
    if actor.approval_id is not None:
        approved = await svc.approved_rows(db, actor.approval_id)
        if approved is None or result.plan.rows_to_score > approved:
            raise ConflictError(
                f"The approved start was for {approved} rows; it would now score "
                f"{result.plan.rows_to_score}. Start it again to ask for a new approval.",
                code="PLAN_CHANGED",
                details={"approved": approved, "now": result.plan.rows_to_score},
            )
    elif result.plan.approval_needed:
        raise ConflictError(
            "This start now needs the operator's approval (P-07). Send it again.",
            code="APPROVAL_REQUIRED",
        )
    run = await svc.start(db, body, result, who, actor)
    out = await svc.run_out(db, run)
    return JSONResponse(status_code=201, content=out.model_dump(mode="json"))


@router.get("", response_model=LabelRunList)
async def list_label_runs(
    input_version_id: str | None = Query(None, max_length=64),
    state: str | None = Query(None, max_length=24),
    kind: str | None = Query(None, max_length=16),
    page: Page = Depends(paging),
    db: AsyncSession = Depends(get_db),
) -> LabelRunList:
    rows, total = await svc.list_runs(
        db,
        input_version_id=input_version_id,
        state=state,
        kind=kind,
        page=page.page,
        limit=page.limit,
    )
    return LabelRunList(
        items=[await svc.run_out(db, r) for r in rows],
        total=total,
        page=page.page,
        limit=page.limit,
    )


@router.post("/aggregate", response_model=LabelRunOut, status_code=201)
async def aggregate_runs(
    body: AggregateRequest,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> LabelRunOut:
    who = await resolve_who(actor, db)
    return await svc.run_out(db, await svc.aggregate(db, body.run_ids, who))


@router.get("/{run_id}", response_model=LabelRunOut)
async def get_label_run(run_id: str, db: AsyncSession = Depends(get_db)) -> LabelRunOut:
    return await svc.run_out(db, await svc.get_run(db, run_id))


@router.get("/{run_id}/labels", response_model=LabelPage)
async def get_labels(
    run_id: str,
    outcome: str | None = Query(None, max_length=128),
    page: int = Query(1, ge=1, le=1_000_000),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> LabelPage:
    return await svc.labels_page(db, run_id, outcome=outcome, page=page, limit=limit)


@router.post("/{run_id}/cancel", response_model=LabelRunOut)
async def cancel_label_run(run_id: str, db: AsyncSession = Depends(get_db)) -> LabelRunOut:
    return await svc.run_out(db, await svc.cancel(db, run_id))


@router.post("/{run_id}/resume", response_model=LabelRunOut)
async def resume_label_run(
    run_id: str, db: AsyncSession = Depends(get_db), actor: Actor = Depends(get_actor)
) -> LabelRunOut:
    """A new job continues the run; no approval (a resume re-sends no counted row, P-07)."""
    who = await resolve_who(actor, db)
    return await svc.run_out(db, await svc.resume(db, run_id, who))


@router.post("/{run_id}/rederive", response_model=LabelRunOut, status_code=201)
async def rederive_label_run(
    run_id: str,
    body: RederiveRequest,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> LabelRunOut:
    who = await resolve_who(actor, db)
    child = await svc.rederive(
        db, run_id, body.threshold_positive, body.threshold_negative, body.min_top_probability, who
    )
    return await svc.run_out(db, child)
