"""Approval routes (ADR-013; Foundation task 9.3). Agents cannot decide (C6)."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.agent_origin import APPROVAL_ACTIONS, Actor, get_actor, resolve_who
from ....core.database import get_db
from ....core.errors import ForbiddenError
from ....core.settings_pin import (
    SETTINGS_ACTIONS,
    check_pin,
    is_settings_change,
    settings_change_targets,
)
from ....models.approval import APPROVAL_STATUSES, Approval
from ....schemas.approvals import (
    ApprovalActionsOut,
    ApprovalList,
    ApprovalOut,
    ApproveBatchItem,
    ApproveBatchOut,
    ApproveBatchRequest,
    RejectRequest,
)
from ....schemas.archive import ArchiveRequest
from ....services import archive_service
from ....services.approval_service import ApprovalService

router = APIRouter(prefix="/api/v1/approvals", tags=["approvals"])

#: ENH-006 D81: the one action a code-rule batch approves (an agent's save of an unapproved hash).
CODE_RULE_ACTION = "code_rule_save"


def _settings_targets(request: Request) -> frozenset[str]:
    """The executor keys of the PIN-gated Settings writes, read once from the live route table."""
    targets: frozenset[str] | None = getattr(request.app.state, "settings_change_targets", None)
    if targets is None:
        targets = settings_change_targets(request.app.routes)
        request.app.state.settings_change_targets = targets
    return targets


def _out(row: Approval, targets: frozenset[str]) -> ApprovalOut:
    out = ApprovalOut.model_validate(row)
    return out.model_copy(
        update={"settings_change": is_settings_change(row.action, row.target, targets)}
    )


def _refuse_agent(actor: Actor) -> None:
    if actor.origin == "agent":
        raise ForbiddenError(
            "An agent cannot approve or reject; the operator decides.", code="AGENT_CANNOT_DECIDE"
        )


@router.get("", response_model=ApprovalList)
async def list_approvals(
    request: Request,
    status: str | None = Query(None, pattern="^(" + "|".join(APPROVAL_STATUSES) + ")$"),
    include_archived: bool = Query(False, description="Also list archived approvals."),
    decided: bool = Query(
        False,
        description="Only decided requests (not pending), the most recent decision first: the "
        "Approvals page's History.",
    ),
    limit: int | None = Query(None, ge=1, le=500, description="At most this many; omit for all."),
    offset: int = Query(0, ge=0, description="Skip this many (Show older)."),
    page: int | None = Query(
        None, ge=1, le=1_000_000, description="Page number (from 1) of `limit` (default 50)."
    ),
    db: AsyncSession = Depends(get_db),
) -> ApprovalList:
    if page is not None:  # 017 FR-017.53: the MCP list rule; the screens use limit/offset
        limit = limit or 50
        offset = (page - 1) * limit
    rows = await ApprovalService.list(
        db, status, include_archived=include_archived, decided=decided, limit=limit, offset=offset
    )
    targets = _settings_targets(request)
    out = ApprovalList(approvals=[_out(a, targets) for a in rows])
    if page is None:
        return out
    every = await ApprovalService.list(
        db, status, include_archived=include_archived, decided=decided
    )
    return out.model_copy(update={"total": len(every), "page": page, "limit": limit})


@router.get("/actions", response_model=ApprovalActionsOut)
async def list_actions() -> ApprovalActionsOut:
    """The registered approval action names."""
    return ApprovalActionsOut(actions=dict(APPROVAL_ACTIONS))


@router.post("/approve-batch", response_model=ApproveBatchOut)
async def approve_batch(
    body: ApproveBatchRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> ApproveBatchOut:
    """Approve every listed waiting request of ONE kind in ONE decision (ENH-006 D79, D81).

    ``kind`` is ``settings`` (a ``config_write`` or ``secret_write`` request through a PIN-gated
    Settings route) or ``code_rules`` (``code_rule_save``); the two are never mixed. Operator only,
    like a single approve. Every id must be a waiting request of that kind; otherwise ``409
    APPROVAL_BATCH_REFUSED`` names each refused id and nothing is approved. The PIN rule is the
    single approve's: the Settings PIN, when set, is asked once for a settings batch; a code-rule
    approval does not ask for it. Each request is then approved through the single approve path
    (it runs once and is recorded on its own row, with the shared ``batch_id``; a code rule's
    approve row is written by its save, as for a single approval); one that fails does not stop
    the others. There is no "reject all"."""
    _refuse_agent(actor)
    eligible: Callable[[Approval], bool]
    if body.kind == "settings":
        await check_pin(request, db)
        targets = _settings_targets(request)
        label = "settings change"

        def eligible(row: Approval) -> bool:
            return is_settings_change(row.action, row.target, targets)

    else:
        label = "code rule"

        def eligible(row: Approval) -> bool:
            return row.action == CODE_RULE_ACTION

    who = await resolve_who(actor, db)
    batch_id, results = await ApprovalService.approve_batch(
        db, body.approval_ids, who.who, eligible=eligible, kind_label=label
    )
    return ApproveBatchOut(
        batch_id=batch_id,
        kind=body.kind,
        results=[ApproveBatchItem.model_validate(r) for r in results],
    )


@router.get("/{approval_id}", response_model=ApprovalOut)
async def get_approval(
    approval_id: str, request: Request, db: AsyncSession = Depends(get_db)
) -> ApprovalOut:
    return _out(await ApprovalService.get(db, approval_id), _settings_targets(request))


@router.post("/{approval_id}/archive", response_model=ApprovalOut)
async def archive_approval(
    approval_id: str,
    body: ArchiveRequest | None = None,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> ApprovalOut:
    """Hide a rejected or expired approval from the default list. Archiving decides nothing."""
    who = await resolve_who(actor, db)
    row = await archive_service.set_archived(
        db, "approval", approval_id, who, archived=True, reason=body.reason if body else None
    )
    return ApprovalOut.model_validate(row)


@router.post("/{approval_id}/unarchive", response_model=ApprovalOut)
async def unarchive_approval(
    approval_id: str,
    body: ArchiveRequest | None = None,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> ApprovalOut:
    who = await resolve_who(actor, db)
    row = await archive_service.set_archived(
        db, "approval", approval_id, who, archived=False, reason=body.reason if body else None
    )
    return ApprovalOut.model_validate(row)


@router.post("/{approval_id}/approve", response_model=ApprovalOut)
async def approve(
    approval_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> ApprovalOut:
    """Run the stored action exactly once. 409 unless the approval is pending.

    Approving a Settings change (``config_write``, ``secret_write``) needs the Settings PIN when
    one is set (feature 014): the approved request then runs without it."""
    _refuse_agent(actor)
    if (await ApprovalService.get(db, approval_id)).action in SETTINGS_ACTIONS:
        await check_pin(request, db)
    who = await resolve_who(actor, db)
    return ApprovalOut.model_validate(await ApprovalService.approve(db, approval_id, who.who))


@router.post("/{approval_id}/reject", response_model=ApprovalOut)
async def reject(
    approval_id: str,
    body: RejectRequest,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> ApprovalOut:
    _refuse_agent(actor)
    who = await resolve_who(actor, db)
    return ApprovalOut.model_validate(
        await ApprovalService.reject(db, approval_id, who.who, body.reason)
    )
