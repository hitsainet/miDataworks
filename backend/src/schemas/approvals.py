"""Approval request and response models. ``secret_payload`` is never part of any of them."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ApprovalOut(BaseModel):
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


class ApprovalList(BaseModel):
    approvals: list[ApprovalOut]


class RejectRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=1000)


class ApprovalActionsOut(BaseModel):
    actions: dict[str, str]
