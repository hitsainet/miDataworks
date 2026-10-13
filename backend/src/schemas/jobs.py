"""Job request and response models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    status: str
    progress: float
    message: str | None
    params: dict[str, Any]
    result: dict[str, Any] | None
    error: str | None
    started_by: str
    started_by_origin: str
    required_model_id: str | None
    queue_reason: str | None
    heartbeat_at: datetime | None
    cancel_requested_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    dismissed_at: datetime | None
    created_at: datetime
    room: str


class JobList(BaseModel):
    jobs: list[JobOut]


class CancelRequest(BaseModel):
    reason: str = Field("Cancelled by the operator.", max_length=500)


class CancelOut(BaseModel):
    job: JobOut
    detail: str


class SelftestRequest(BaseModel):
    """Parameters of the walking-skeleton job."""

    duration_seconds: float = Field(5.0, ge=0, le=3600)
    rows: int = Field(1000, ge=1, le=1_000_000)
    step_seconds: float = Field(0.5, gt=0, le=60)
    required_model_id: str | None = Field(None, max_length=255)
    check_hf_token: bool = Field(
        False, description="Resolve the stored Hugging Face token in the worker (secret test)"
    )
