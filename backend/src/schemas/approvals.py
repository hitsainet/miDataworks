"""Approval request and response models. ``secret_payload`` is never part of any of them."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .archive import ArchiveFields


class ApprovalOut(ArchiveFields):
    model_config = ConfigDict(from_attributes=True)

    id: str
    action: str
    target: str
    summary: str
    payload: dict[str, Any]
    request_digest: str
    requested_by: str
    status: str
    expires_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    reason: str | None
    result_kind: str | None
    result_id: str | None
    error: dict[str, Any] | None
    created_at: datetime
    #: ENH-006 D79: set when the operator approved this request in one decision with others.
    batch_id: str | None = None
    #: ENH-006 D79: a Settings change (a request whose route is behind the Settings PIN), so it
    #: may be approved with the other waiting settings changes in one decision.
    settings_change: bool = False


class ApprovalList(BaseModel):
    approvals: list[ApprovalOut]
    #: 017 FR-017.53: set when the caller asked for a page.
    total: int | None = None
    page: int | None = None
    limit: int | None = None


class RejectRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=1000)


class ApprovalActionsOut(BaseModel):
    actions: dict[str, str]


#: What one batch approves (ENH-006 D79 settings changes, D81 code rules); never mixed.
BatchKind = Literal["settings", "code_rules"]


class ApproveBatchRequest(BaseModel):
    """ENH-006 D79/D81: the waiting requests of ONE kind the operator approves in one decision."""

    approval_ids: list[str] = Field(..., min_length=1, max_length=500)
    kind: BatchKind = "settings"


class ApproveBatchItem(BaseModel):
    approval_id: str
    #: The request's status after this decision: ``executed``, ``failed``, or the status that
    #: refused it when another decision got there first.
    status: str
    result_kind: str | None = None
    result_id: str | None = None
    error: dict[str, Any] | None = None


class ApproveBatchOut(BaseModel):
    batch_id: str
    kind: BatchKind
    results: list[ApproveBatchItem]
