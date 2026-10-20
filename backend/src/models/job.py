"""``dw_jobs``: one record for every long job (ADR-007; Foundation task 5.1).

Kind-specific tables (label runs, publishes) reference this row. Status is a CHECK-constrained
string rather than a native PostgreSQL enum: extending a native enum needs a non-transactional
``ALTER TYPE``, which miStudio had to work around on three tables.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Float, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..core.database import Base

JOB_STATUSES: tuple[str, ...] = (
    "queued",
    "running",
    "cancelling",
    "cancelled",
    "completed",
    "failed",
)
TERMINAL_STATUSES: frozenset[str] = frozenset({"cancelled", "completed", "failed"})
ORIGINS: tuple[str, ...] = ("operator", "agent")


class Job(Base):
    __tablename__ = "dw_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in JOB_STATUSES) + ")", name="status_valid"
        ),
        CheckConstraint(
            "started_by_origin IN (" + ", ".join(f"'{o}'" for o in ORIGINS) + ")",
            name="origin_valid",
        ),
        CheckConstraint("length(started_by) > 0", name="started_by_present"),
        CheckConstraint("progress >= 0 AND progress <= 100", name="progress_range"),
        Index("ix_dw_jobs_status_kind", "status", "kind"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    message: Mapped[str | None] = mapped_column(Text)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    started_by: Mapped[str] = mapped_column(Text, nullable=False)
    started_by_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    celery_task_id: Mapped[str | None] = mapped_column(String(255))
    #: The miLLM model this job needs, if any (R-03.64; task 7.1).
    required_model_id: Mapped[str | None] = mapped_column(String(255), index=True)
    #: Why a queued job has not started, shown in the UI.
    queue_reason: Mapped[str | None] = mapped_column(Text)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES
