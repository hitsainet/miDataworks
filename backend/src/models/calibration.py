"""Calibration sets, records, checks, verdicts and targets (FR-006.1 – FR-006.21, FR-006.43;
FTDD 006 section 4.2).

- A calibration set is immutable and created complete in one transaction; its human labels are
  materialised in ``dw_calibration_set_labels`` in version row order (``position``).
- A record is written by the worker at the end of its job, with its checks and verdict, in one
  transaction, and is APPEND-ONLY (trigger ``dw_append_only``, migration 0014).
- A target row is never updated; the latest per question wins. An agent row carries the approval
  and the approving operator (S3-08), enforced by a CHECK.

Two deliberate departures from FTDD 006 section 4.2, recorded in the controls review: the set's
uniqueness includes the question (Humicroedit calibrates two wordings against one mapping), and a
check row is keyed by (record, check, METRIC), because ``both_classes`` and ``row_alignment`` each
guard two metrics.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..core.database import Base
from .enums import CalibrationSource, CheckResultValue, GateRule, GateVerdict, ScoreKind, check_in

_ORIGIN = "IN ('operator', 'agent')"
_LICENCE = "IN ('permits_redistribution', 'private_only', 'forbids_redistribution')"


class CalibrationSet(Base):
    __tablename__ = "dw_calibration_sets"
    __table_args__ = (
        CheckConstraint(check_in("source_kind", CalibrationSource), name="source_kind_valid"),
        CheckConstraint(
            "source_kind <> 'review' OR source_queue_id IS NOT NULL", name="review_has_queue"
        ),
        CheckConstraint(f"licence_class {_LICENCE}", name="licence_class_valid"),
        CheckConstraint(f"created_by_origin {_ORIGIN}", name="origin_valid"),
        CheckConstraint("length(created_by) > 0", name="created_by_present"),
        CheckConstraint("question_hash ~ '^[0-9a-f]{64}$'", name="question_hash_pattern"),
        CheckConstraint("mapping_hash ~ '^[0-9a-f]{64}$'", name="mapping_hash_pattern"),
        UniqueConstraint(
            "version_id", "question_hash", "mapping_hash", name="uq_dw_calibration_sets_identity"
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    version_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_versions.id", ondelete="RESTRICT"), nullable=False
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    question_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Ordered labels; the first is the positive class of a binary set.
    label_set: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    source_queue_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("dw_review_queues.id", ondelete="RESTRICT")
    )
    #: ``dw.calibration-mapping/v1``, canonical.
    mapping: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    mapping_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: NULL when no per-rater column is declared (FR-006.4).
    ratings_sorted: Mapped[bool | None] = mapped_column(Boolean)
    #: From 008's licence table at creation (P-14).
    licence_class: Mapped[str] = mapped_column(String(32), nullable=False)
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CalibrationSetLabel(Base):
    __tablename__ = "dw_calibration_set_labels"
    __table_args__ = (
        CheckConstraint("row_key ~ '^[0-9a-f]{64}$'", name="row_key_pattern"),
        UniqueConstraint(
            "calibration_set_id", "position", name="uq_dw_calibration_set_labels_position"
        ),
    )

    calibration_set_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("dw_calibration_sets.id", ondelete="CASCADE"), primary_key=True
    )
    row_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    #: Version row order; every array is built in this order.
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    #: NULL = excluded by the mapping (the middle band, an unmapped value).
    human_label: Mapped[str | None] = mapped_column(Text)
    group_key: Mapped[str | None] = mapped_column(Text)
    #: Canonical JSON of the stratum columns' values.
    strata_key: Mapped[str | None] = mapped_column(Text)
    is_reference: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    ratings: Mapped[list[int] | None] = mapped_column(ARRAY(SmallInteger))
    decision_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("dw_review_decisions.id", ondelete="RESTRICT")
    )


class CalibrationTarget(Base):
    __tablename__ = "dw_calibration_targets"
    __table_args__ = (
        CheckConstraint("target > 0.5 AND target < 1", name="target_range"),
        CheckConstraint(f"set_by_origin {_ORIGIN}", name="origin_valid"),
        CheckConstraint("length(set_by) > 0", name="set_by_present"),
        CheckConstraint(
            "(set_by_origin = 'agent') = (approval_id IS NOT NULL AND approved_by IS NOT NULL)",
            name="agent_iff_approved",
        ),
        CheckConstraint("question_hash ~ '^[0-9a-f]{64}$'", name="question_hash_pattern"),
        Index("ix_dw_calibration_targets_question_created", "question_hash", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    question_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    target: Mapped[float] = mapped_column(Float, nullable=False)
    set_by: Mapped[str] = mapped_column(Text, nullable=False)
    set_by_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    approval_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("dw_approvals.id", ondelete="RESTRICT")
    )
    approved_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )


class CalibrationRecord(Base):
    """Keyed by (labeler identity, calibration set); never updated or deleted."""

    __tablename__ = "dw_calibration_records"
    __table_args__ = (
        CheckConstraint(check_in("score_kind", ScoreKind), name="score_kind_valid"),
        CheckConstraint(f"created_by_origin {_ORIGIN}", name="origin_valid"),
        CheckConstraint("length(created_by) > 0", name="created_by_present"),
        CheckConstraint("metrics_sha256 ~ '^[0-9a-f]{64}$'", name="metrics_sha256_pattern"),
        Index("ix_dw_calibration_records_identity_created", "labeler_identity_hash", "created_at"),
        Index("ix_dw_calibration_records_fingerprint", "labeler_fingerprint"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    calibration_set_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("dw_calibration_sets.id", ondelete="RESTRICT"), nullable=False
    )
    label_run_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("dw_label_runs.id", ondelete="RESTRICT"), nullable=False
    )
    labeler_identity: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    labeler_identity_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    labeler_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    score_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    metrics_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Seeds, resamples, draws, bins, code version.
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    #: Domain warning (FR-006.36), dropped resamples.
    warnings: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    job_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("dw_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )


class CalibrationCheck(Base):
    __tablename__ = "dw_calibration_checks"
    __table_args__ = (CheckConstraint(check_in("result", CheckResultValue), name="result_valid"),)

    record_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("dw_calibration_records.id", ondelete="RESTRICT"), primary_key=True
    )
    check_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    metric_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    check_version: Mapped[int] = mapped_column(Integer, nullable=False)
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    statistic: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    rule: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)


class CalibrationVerdict(Base):
    __tablename__ = "dw_calibration_verdicts"
    __table_args__ = (
        CheckConstraint(check_in("verdict", GateVerdict), name="verdict_valid"),
        CheckConstraint("rule IS NULL OR " + check_in("rule", GateRule), name="rule_valid"),
    )

    record_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("dw_calibration_records.id", ondelete="RESTRICT"), primary_key=True
    )
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    rule: Mapped[str | None] = mapped_column(String(16))
    numbers: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    target_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("dw_calibration_targets.id", ondelete="RESTRICT")
    )
