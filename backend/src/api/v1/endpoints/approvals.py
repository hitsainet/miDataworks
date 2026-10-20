"""Approval routes (ADR-013; Foundation task 9.3). Agents cannot decide (C6)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.agent_origin import APPROVAL_ACTIONS, Actor, get_actor, resolve_who
from ....core.database import get_db
from ....core.errors import ForbiddenError
from ....models.approval import APPROVAL_STATUSES
from ....schemas.approvals import ApprovalActionsOut, ApprovalList, ApprovalOut, RejectRequest
from ....schemas.archive import ArchiveRequest
from ....services import archive_service
from ....services.approval_service import ApprovalService

router = APIRouter(prefix="/api/v1/approvals", tags=["approvals"])


def _refuse_agent(actor: Actor) -> None:
    if actor.origin == "agent":
        raise ForbiddenError(
            "An agent cannot approve or reject; the operator decides.", code="AGENT_CANNOT_DECIDE"
        )


@router.get("", response_model=ApprovalList)
async def list_approvals(
    status: str | None = Query(None, pattern="^(" + "|".join(APPROVAL_STATUSES) + ")$"),
    include_archived: bool = Query(False, description="Also list archived approvals."),
    db: AsyncSession = Depends(get_db),
) -> ApprovalList:
    rows = await ApprovalService.list(db, status, include_archived=include_archived)
    return ApprovalList(approvals=[ApprovalOut.model_validate(a) for a in rows])


@router.get("/actions", response_model=ApprovalActionsOut)
async def list_actions() -> ApprovalActionsOut:
    """The registered approval action names."""
    return ApprovalActionsOut(actions=dict(APPROVAL_ACTIONS))


@router.get("/{approval_id}", response_model=ApprovalOut)
async def get_approval(approval_id: str, db: AsyncSession = Depends(get_db)) -> ApprovalOut:
    return ApprovalOut.model_validate(await ApprovalService.get(db, approval_id))


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
    approval_id: str, db: AsyncSession = Depends(get_db), actor: Actor = Depends(get_actor)
) -> ApprovalOut:
    """Run the stored action exactly once. 409 unless the approval is pending."""
    _refuse_agent(actor)
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
