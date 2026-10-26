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
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.agent_origin import Actor, get_actor, requires_approval_when_agent, resolve_who
from ....core.database import get_db
from ....core.errors import AppError, ConflictError, UnprocessableError
from ....core.key_binding import url_origin
from ....schemas.archive import ArchiveRequest
from ....schemas.labeling import (
    AggregateRequest,
    HandoffReview,
    LabelAgreementOut,
    LabelPage,
    LabelRunList,
    LabelRunOut,
    LabelRunStart,
    Plan,
    RederiveRequest,
)
from ....services import agent_label_ledger, archive_service, label_agreement
from ....services import label_run_service as svc
from ....services.labeling_ports import register_bindings
from ....services.review import handoff
from ._ids import uuid_filter
from ._paging import Page, paging

router = APIRouter(prefix="/api/v1/label-runs", tags=["labeling"])

register_bindings()


def _paid(plan: Plan) -> bool:
    """017 FR-017.40: the plan runs on a paid endpoint (``services/labeling/cost.py``)."""
    return bool((plan.cost or {}).get("paid"))


async def _needs_approval(values: dict[str, Any], db: AsyncSession) -> bool:
    """The gate's predicate: over the P-07 window, or (017 FR-017.40) ANY run on a paid
    endpoint. A request the plan REFUSES is not gated: the route then refuses it the same way,
    and no approval is created for a run that could never start (FR-005.54). Anything else that
    raises fails CLOSED in the decorator."""
    body = values["body"]
    assert isinstance(body, LabelRunStart)
    try:
        result = await svc.plan(db, body, "agent")
    except AppError:
        return False
    return result.plan.approval_needed or _paid(result.plan)


def _text_leaves(endpoint: str | None) -> str:
    return f"The selected rows' text is sent to {endpoint or 'the endpoint'}."


async def _approval_facts(values: dict[str, Any], db: AsyncSession) -> dict[str, Any]:
    """The plan the operator approves (shown on the card, bound by the digest); for a paid run
    also the rows, the cap, the cost and where the rows' text goes (017 FTASKS 9.7)."""
    body = values["body"]
    assert isinstance(body, LabelRunStart)
    result = await svc.plan(db, body, "agent")
    facts: dict[str, Any] = {"plan": result.plan.model_dump(mode="json")}
    if _paid(result.plan):
        cost = result.plan.cost or {}
        endpoint = url_origin(result.resolved.base_url)
        facts["paid"] = {
            "rows": result.plan.rows_to_score,
            "cap_rows": cost.get("cap_rows"),
            "price_per_call_usd": cost.get("price_per_call_usd"),
            "questions": cost.get("questions"),
            "estimated_usd": cost.get("estimated_usd"),
            "spent_usd": cost.get("spent_usd"),
            "ceiling_usd": cost.get("ceiling_usd"),
            "endpoint": endpoint,
            "text_leaves": _text_leaves(endpoint),
        }
    return facts


def _action_for(values: dict[str, Any], payload: dict[str, Any]) -> str:
    """``paid_classifier_run`` when the plan the operator approves is paid (FTID §7.7)."""
    return "paid_classifier_run" if payload.get("paid") else "agent_label_rows"


def _summary(payload: dict[str, Any]) -> str:
    plan = payload.get("plan") or {}
    paid = payload.get("paid")
    if paid:
        return (
            f"Paid classifier run: {paid.get('rows')} rows of version "
            f"{(payload.get('body') or {}).get('input_version_id', '?')}, about "
            f"${paid.get('estimated_usd')} (cap {paid.get('cap_rows')} rows per run). "
            f"{paid.get('text_leaves')}"
        )
    return (
        f"Label {plan.get('rows_to_score', '?')} rows of version "
        f"{(payload.get('body') or {}).get('input_version_id', '?')} "
        f"({plan.get('agent_window_rows', 0)} agent rows already in the last 24 h)"
    )


async def _resume_is_paid(values: dict[str, Any], db: AsyncSession) -> bool:
    """017 FR-017.41: an agent's resume of a run on a paid endpoint waits for the operator. A
    resume that would be refused is not gated (the route refuses it the same way)."""
    try:
        run = await svc.get_run(db, str(values["run_id"]))
        if run.kind not in ("classifier", "judge"):
            return False
        facts = await svc.remainder(db, run)
    except AppError:
        return False
    return bool(facts["paid"])


async def _remainder_facts(values: dict[str, Any], db: AsyncSession) -> dict[str, Any]:
    """What the operator approves for a resume: the REMAINING rows only, and their cost."""
    facts = await svc.remainder(db, await svc.get_run(db, str(values["run_id"])))
    return {"remainder": {**facts, "text_leaves": _text_leaves(facts.get("endpoint"))}}


def _resume_summary(payload: dict[str, Any]) -> str:
    left = payload.get("remainder") or {}
    return (
        f"Resume paid classifier run {payload.get('run_id')}: the remaining "
        f"{left.get('rows_remaining')} of {left.get('rows_total')} rows, about "
        f"${left.get('estimated_usd')}. {left.get('text_leaves')}"
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
    "agent_label_rows",
    actions=("paid_classifier_run",),
    action_for=_action_for,
    when=_needs_approval,
    enrich=_approval_facts,
    summary=_summary,
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
        if _paid(result.plan) and await svc.approved_action(db, actor.approval_id) != (
            "paid_classifier_run"
        ):
            raise ConflictError(
                "The endpoint became paid after this start was approved; start it again to ask "
                "the operator for a paid run.",
                code="PLAN_CHANGED",
                details={"now": "paid_classifier_run"},
            )
        # Review R4: the approved paid terms bind the run (where the text goes, what it costs).
        changed = await svc.paid_terms_changed(
            db,
            actor.approval_id,
            "paid",
            url_origin(result.resolved.base_url),
            (result.plan.cost or {}).get("estimated_usd") if _paid(result.plan) else None,
        )
        if changed is not None:
            raise ConflictError(
                "The endpoint or its price changed after this paid start was approved; start it "
                "again to ask the operator for the new terms.",
                code="PLAN_CHANGED",
                details=changed,
            )
    elif who.origin == "agent" and (result.plan.approval_needed or _paid(result.plan)):
        raise ConflictError(
            "This start now needs the operator's approval (P-07; a paid endpoint, 017 "
            "FR-017.40). Send it again.",
            code="APPROVAL_REQUIRED",
        )
    run = await svc.start(db, body, result, who, actor)
    out = await svc.run_out(db, run)
    return JSONResponse(status_code=201, content=out.model_dump(mode="json"))


@router.post("/handoffs/{handoff_id}/review", status_code=201)
async def review_disagreements(
    handoff_id: str = Path(..., min_length=1, max_length=40),
    body: HandoffReview | None = None,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> dict[str, Any]:
    """017 FR-017.44: open label-review queues over exactly the rows where the hand-off's runs
    disagree with their compared column, at most 5,000 rows a queue, every stratum in the first,
    in one transaction; over REVIEW_MAX_QUEUES_PER_HANDOFF queues is refused. An agent may open
    them; only the operator decides rows (P-10)."""
    who = await resolve_who(actor, db)
    body = body or HandoffReview()
    return await handoff.hand_off(
        db, handoff_id, who, column=body.column, positive_value=body.positive_value, by=body.by
    )


@router.get("", response_model=LabelRunList)
async def list_label_runs(
    input_version_id: uuid.UUID | None = Query(None),
    state: str | None = Query(None, max_length=24),
    kind: str | None = Query(None, max_length=16),
    include_archived: bool = Query(False, description="Also list archived runs."),
    page: Page = Depends(paging),
    db: AsyncSession = Depends(get_db),
) -> LabelRunList:
    rows, total = await svc.list_runs(
        db,
        input_version_id=uuid_filter(input_version_id),
        state=state,
        kind=kind,
        page=page.page,
        limit=page.limit,
        include_archived=include_archived,
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


@router.get("/{run_id}/agreement", response_model=LabelAgreementOut)
async def get_agreement(
    run_id: str,
    column: str | None = Query(None, max_length=256),
    positive_value: str | None = Query(None, max_length=256),
    by: list[str] | None = Query(None, max_length=8, description="Strata columns (017)"),
    limit: int = Query(label_agreement.DEFAULT_CELL_LIMIT, ge=1, le=label_agreement.MAX_CELL_LIMIT),
    page_token: str | None = Query(None, max_length=64),
    handoff_id: str | None = Query(None, max_length=40),
    db: AsyncSession = Depends(get_db),
) -> LabelAgreementOut:
    """The run against a two-valued label column its input version already has (the run's
    ``target_column`` when none is named): confusion counts, agreement, each side's positive
    share, AUROC from the run's own score, and a note in plain words. 017 FR-017.43: per stratum
    of ``by`` (default genre, generator, kind, form where present) the same plus Cohen's kappa,
    worst cells first, paged; a partial run says so with each stratum's coverage."""
    run = await svc.get_run(db, run_id)
    return LabelAgreementOut(
        **await label_agreement.agreement(
            db,
            run,
            column,
            positive_value,
            by=by,
            limit=limit,
            page_token=page_token,
            handoff_id=handoff_id,
        )
    )


@router.get("/{run_id}/labels", response_model=LabelPage)
async def get_labels(
    run_id: str,
    outcome: str | None = Query(None, max_length=128),
    page: int = Query(1, ge=1, le=1_000_000),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> LabelPage:
    return await svc.labels_page(db, run_id, outcome=outcome, page=page, limit=limit)


@router.post("/{run_id}/archive", response_model=LabelRunOut)
async def archive_label_run(
    run_id: str,
    body: ArchiveRequest | None = None,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> LabelRunOut:
    """Hide a finished run from default lists and pickers; its labels stay readable."""
    who = await resolve_who(actor, db)
    run = await archive_service.set_archived(
        db, "label_run", run_id, who, archived=True, reason=body.reason if body else None
    )
    return await svc.run_out(db, run)


@router.post("/{run_id}/unarchive", response_model=LabelRunOut)
async def unarchive_label_run(
    run_id: str,
    body: ArchiveRequest | None = None,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> LabelRunOut:
    who = await resolve_who(actor, db)
    run = await archive_service.set_archived(
        db, "label_run", run_id, who, archived=False, reason=body.reason if body else None
    )
    return await svc.run_out(db, run)


@router.post("/{run_id}/cancel", response_model=LabelRunOut)
async def cancel_label_run(run_id: str, db: AsyncSession = Depends(get_db)) -> LabelRunOut:
    return await svc.run_out(db, await svc.cancel(db, run_id))


@router.post(
    "/{run_id}/resume",
    response_model=LabelRunOut,
    responses={202: {"description": "Waiting for the operator's approval (a paid run)"}},
)
@requires_approval_when_agent(
    "paid_classifier_run",
    when=_resume_is_paid,
    enrich=_remainder_facts,
    summary=_resume_summary,
)
async def resume_label_run(
    run_id: str, db: AsyncSession = Depends(get_db), actor: Actor = Depends(get_actor)
) -> Any:
    """A new job continues the run. No approval for a resume of an unpaid run (it re-sends no
    counted row, P-07); an agent's resume of a PAID run waits for the operator, who approves the
    remaining rows only (017 FR-017.41)."""
    who = await resolve_who(actor, db)
    if (
        who.origin == "agent"
        and actor.approval_id is None
        and await _resume_is_paid({"run_id": run_id}, db)
    ):
        raise ConflictError(
            "Resuming a paid run needs the operator's approval (017 FR-017.41). Send it again.",
            code="APPROVAL_REQUIRED",
        )
    if actor.approval_id is not None:
        # Review R4: the approved remainder binds the resume (where the text goes, its cost).
        left = await svc.remainder(db, await svc.get_run(db, run_id))
        changed = await svc.paid_terms_changed(
            db,
            actor.approval_id,
            "remainder",
            left.get("endpoint"),
            left.get("estimated_usd") if left.get("paid") else None,
        )
        if changed is not None:
            raise ConflictError(
                "The endpoint or its price changed after this resume was approved; resume it "
                "again to ask the operator for the new terms.",
                code="PLAN_CHANGED",
                details=changed,
            )
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
