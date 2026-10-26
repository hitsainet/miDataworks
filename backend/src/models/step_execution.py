"""``dw_step_executions``: one computed step, reusable by identity (FR-002.28, FR-002.47).

FTDD 002 section 4.2. A completed execution with an ``identity_digest`` is reused by any later
build that reaches the same identity (a partial unique index allows one completed execution per
identity). Index 0 of a build is the ``assemble`` step; the rest run through feature 003.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..core.database import Base
from .enums import StepKind, StepState, check_in

#: The executions an identity is reused from or waited on (0044 adds ``NOT reuse_blocked``).
LIVE_WHERE = "state IN ('queued', 'running', 'completed') AND NOT reuse_blocked"


class StepExecution(Base):
    __tablename__ = "dw_step_executions"
    __table_args__ = (
        CheckConstraint(check_in("kind", StepKind), name="kind_valid"),
        CheckConstraint(check_in("state", StepState), name="state_valid"),
        CheckConstraint(
            "(kind = 'assemble' AND operator_name IS NULL) OR "
            "(kind = 'operator' AND operator_name IS NOT NULL AND operator_version IS NOT NULL "
            "AND manifest_hash IS NOT NULL)",
            name="operator_fields",
        ),
        CheckConstraint("identity_digest ~ '^[0-9a-f]{64}$'", name="identity_pattern"),
        # One live-or-completed execution per identity, so two builds sharing a prefix compute it
        # once (FTASKS 8.2). Stricter than FTDD's "completed only": a queued duplicate would be
        # computed twice. Failed and cancelled executions do not block a retry. Feature 018
        # (0044, decision I7): a completed step whose code rules met a host fault is
        # ``reuse_blocked`` and leaves the index, so the next build computes it afresh.
        Index(
            "uq_dw_step_executions_identity_live",
            "identity_digest",
            unique=True,
            postgresql_where=text(LIVE_WHERE),
        ),
        Index("ix_dw_step_executions_job", "job_id"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    identity_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    operator_name: Mapped[str | None] = mapped_column(String(64))
    operator_version: Mapped[str | None] = mapped_column(String(64))
    manifest_hash: Mapped[str | None] = mapped_column(String(64))
    params_hash: Mapped[str | None] = mapped_column(String(64))
    step_seed: Mapped[int | None] = mapped_column(BigInteger)
    bindings_digest: Mapped[str | None] = mapped_column(String(64))
    #: The identity of the execution whose output this one read (see services/identity.py).
    input_execution_digest: Mapped[str | None] = mapped_column(String(64))
    input_logical_digest: Mapped[str | None] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Relative to ``DATA_DIR``: ``runs/<job_id>/steps/<id>``.
    output_dir: Mapped[str] = mapped_column(Text, nullable=False)
    output_logical_digest: Mapped[str | None] = mapped_column(String(64))
    output_column_roles: Mapped[dict[str, str] | None] = mapped_column(JSONB)
    split_roles: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    #: The operator's reports (one per batch it ran), from the step's meta.json; NULL before 0026.
    reports: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    rows_in: Mapped[int | None] = mapped_column(BigInteger)
    rows_kept: Mapped[int | None] = mapped_column(BigInteger)
    rows_changed: Mapped[int | None] = mapped_column(BigInteger)
    rows_dropped: Mapped[int | None] = mapped_column(BigInteger)
    rows_added: Mapped[int | None] = mapped_column(BigInteger)
    rows_split_assigned: Mapped[int | None] = mapped_column(BigInteger)
    #: Drop and change counts by reason, stored at ingestion for the drop log.
    reason_counts: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    job_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("dw_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    #: Feature 018 (0044, I7): its code rules met a host fault (``rule_worker_unavailable``,
    #: ``rule_timeout``), so its rows are not the rules' answer and it is never reused.
    reuse_blocked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
