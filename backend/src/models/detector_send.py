"""Sends of a detector set to miStudio, their steps, and the durable registration map
(009 FR-009.16 - FR-009.30, FR-009.78, FR-009.79, FR-009.82; FTDD 009 sections 2.2, 4.1).

- ``dw_detector_sends.snapshot``, ``snapshot_sha256``, ``checks``, ``plan`` and ``approval_digest``
  are written once. A ``BEFORE UPDATE`` trigger (``dw_detector_sends_frozen``, migration 0016)
  refuses any change to them, so a later edit of the set can never rewrite what a send used
  (FR-009.12, FR-009.16).
- A step row is keyed by ``(send_id, step, unit_key)``: the version ID for ``publish``,
  ``repo|config|split`` for ``download`` and the role ID for ``register``. A resumed send reads
  these rows and never repeats a ``done`` or ``reused`` one (FR-009.27).
- ``dw_mistudio_registrations`` outlives sends: it is how a re-send of an unchanged version reuses
  miStudio's dataset (FR-009.78) and how P-21 traces a probe dataset ID back to rows.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
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

SEND_STATES: tuple[str, ...] = (
    "awaiting_approval",
    "queued",
    "running",
    "completed",
    "failed",
    "cancelled",
    "rejected",
)
ACTIVE_SEND_STATES: tuple[str, ...] = ("queued", "running")
STEP_KINDS: tuple[str, ...] = ("publish", "download", "register")
STEP_STATES: tuple[str, ...] = ("pending", "running", "done", "reused", "failed")
REGISTRATION_KINDS: tuple[str, ...] = ("dataset", "view")

_ORIGIN = "IN ('operator', 'agent')"


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class DetectorSend(Base):
    __tablename__ = "dw_detector_sends"
    __table_args__ = (
        CheckConstraint(_in("state", SEND_STATES), name="state_valid"),
        CheckConstraint("visibility IN ('private', 'public')", name="visibility_valid"),
        CheckConstraint(f"started_by_origin {_ORIGIN}", name="origin_valid"),
        CheckConstraint("length(started_by) > 0", name="started_by_present"),
        CheckConstraint("snapshot_sha256 ~ '^[0-9a-f]{64}$'", name="snapshot_sha256_pattern"),
        CheckConstraint("approval_digest ~ '^[0-9a-f]{64}$'", name="approval_digest_pattern"),
        CheckConstraint(
            "started_by_origin <> 'agent' OR approval_id IS NOT NULL", name="agent_has_approval"
        ),
        Index("ix_dw_detector_sends_set", "set_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    set_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("dw_detector_sets.id", ondelete="RESTRICT"), nullable=False
    )
    #: The job that runs (or last ran) the send; a resume creates a new job (ADR-007).
    job_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("dw_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    #: Every job this send has had, oldest first.
    job_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    notes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    #: The send plan: publish units with their 008 request and digest, download units, and the
    #: exact registration body per role. Bound by ``approval_digest`` (FR-009.82).
    plan: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, default="private")
    mistudio_base_url: Mapped[str] = mapped_column(Text, nullable=False)
    approval_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    approval_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("dw_approvals.id", ondelete="RESTRICT")
    )
    started_by: Mapped[str] = mapped_column(Text, nullable=False)
    started_by_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(Text)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DetectorSendStep(Base):
    __tablename__ = "dw_detector_send_steps"
    __table_args__ = (
        CheckConstraint(_in("step", STEP_KINDS), name="step_valid"),
        CheckConstraint(_in("state", STEP_STATES), name="state_valid"),
    )

    send_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("dw_detector_sends.id", ondelete="CASCADE"), primary_key=True
    )
    step: Mapped[str] = mapped_column(String(16), primary_key=True)
    unit_key: Mapped[str] = mapped_column(Text, primary_key=True)
    #: Order the worker walks steps in (publish units, then downloads, then roles).
    position: Mapped[int] = mapped_column(nullable=False, default=0)
    role_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    publish_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("dw_publishes.id", ondelete="RESTRICT")
    )
    repo_id: Mapped[str | None] = mapped_column(Text)
    commit: Mapped[str | None] = mapped_column(String(40))
    mistudio_dataset_id: Mapped[str | None] = mapped_column(Text)
    probe_dataset_id: Mapped[str | None] = mapped_column(Text)
    expected_counts: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    registered_counts: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    #: The exact registration body sent (no secret can appear: keys are pinned, FTDD section 8).
    request_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    response_sha256: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MiStudioRegistration(Base):
    """A durable map of what miStudio holds for miDataworks (FR-009.78, FR-009.79)."""

    __tablename__ = "dw_mistudio_registrations"
    __table_args__ = (
        CheckConstraint(_in("kind", REGISTRATION_KINDS), name="kind_valid"),
        CheckConstraint(
            "kind <> 'dataset' OR (repo_id IS NOT NULL AND split IS NOT NULL "
            "AND commit IS NOT NULL)",
            name="dataset_fields",
        ),
        CheckConstraint(
            "kind <> 'view' OR (probe_dataset_id IS NOT NULL AND role IS NOT NULL "
            "AND mapping_sha256 IS NOT NULL AND columns_sha256 IS NOT NULL)",
            name="view_fields",
        ),
        Index(
            "uq_dw_mistudio_registrations_dataset",
            "mistudio_base_url",
            "repo_id",
            text("coalesce(config, '')"),
            "split",
            "commit",
            unique=True,
            postgresql_where=text("kind = 'dataset'"),
        ),
        Index(
            "uq_dw_mistudio_registrations_view",
            "mistudio_base_url",
            "mistudio_dataset_id",
            "role",
            "mapping_sha256",
            "columns_sha256",
            unique=True,
            postgresql_where=text("kind = 'view'"),
        ),
        # P-21 tracing: probe dataset ID -> version and split.
        Index("ix_dw_mistudio_registrations_probe_dataset", "probe_dataset_id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    mistudio_base_url: Mapped[str] = mapped_column(Text, nullable=False)
    repo_id: Mapped[str | None] = mapped_column(Text)
    config: Mapped[str | None] = mapped_column(Text)
    split: Mapped[str | None] = mapped_column(Text)
    commit: Mapped[str | None] = mapped_column(String(40))
    mistudio_dataset_id: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str | None] = mapped_column(String(32))
    mapping_sha256: Mapped[str | None] = mapped_column(String(64))
    columns_sha256: Mapped[str | None] = mapped_column(String(64))
    probe_dataset_id: Mapped[str | None] = mapped_column(Text)
    version_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_versions.id", ondelete="RESTRICT"), nullable=False
    )
    send_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("dw_detector_sends.id", ondelete="RESTRICT")
    )
    registered_counts: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
