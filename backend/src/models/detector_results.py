"""Results snapshots, reward marks, agreement reports and cached length profiles
(009 FR-009.9, FR-009.31 - FR-009.41, FR-009.52 - FR-009.56, FR-009.69; FTDD 009 section 4.1).

All four tables are INSERT-ONLY (``dw_append_only()`` triggers, migration 0016):

- a results snapshot is evidence of what miStudio said at a time; miStudio prunes rows, so a
  snapshot is never rewritten (FR-009.40);
- a reward mark has no unmark route and no update (decision TQ9): a probe used as a training reward
  stays one (R-03.53);
- a length profile describes an immutable version split; ``measure_version`` changes when the length
  measure changes, which gives a new row, never an edit.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..core.database import Base

_ORIGIN = "IN ('operator', 'agent')"
LENGTH_UNITS: tuple[str, ...] = ("chars", "words")
MARK_SOURCES: tuple[str, ...] = ("export", "operator", "agent")


class DetectorResults(Base):
    __tablename__ = "dw_detector_results"
    __table_args__ = (
        CheckConstraint(f"read_by_origin {_ORIGIN}", name="origin_valid"),
        CheckConstraint("length(read_by) > 0", name="read_by_present"),
        Index("ix_dw_detector_results_set", "set_id", "read_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    set_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("dw_detector_sets.id", ondelete="RESTRICT"), nullable=False
    )
    send_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("dw_detector_sends.id", ondelete="RESTRICT"), nullable=False
    )
    mistudio_base_url: Mapped[str] = mapped_column(Text, nullable=False)
    read_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    read_by: Mapped[str] = mapped_column(Text, nullable=False)
    read_by_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    runs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    figures: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    report_sha256: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    gone: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class RewardMark(Base):
    __tablename__ = "dw_reward_marks"
    __table_args__ = (
        CheckConstraint(
            "source IN (" + ", ".join(f"'{s}'" for s in MARK_SOURCES) + ")", name="source_valid"
        ),
        CheckConstraint(f"marked_by_origin {_ORIGIN}", name="origin_valid"),
        CheckConstraint("length(marked_by) > 0", name="marked_by_present"),
        CheckConstraint("length(reason) > 0", name="reason_present"),
        UniqueConstraint("mistudio_base_url", "mistudio_probe_id", name="uq_dw_reward_marks_probe"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    mistudio_base_url: Mapped[str] = mapped_column(Text, nullable=False)
    mistudio_probe_id: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    export_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("dw_exports.id", ondelete="RESTRICT")
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    marked_by: Mapped[str] = mapped_column(Text, nullable=False)
    marked_by_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    marked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AgreementReport(Base):
    __tablename__ = "dw_agreement_reports"
    __table_args__ = (
        CheckConstraint(f"created_by_origin {_ORIGIN}", name="origin_valid"),
        CheckConstraint("length(created_by) > 0", name="created_by_present"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    version_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_versions.id", ondelete="RESTRICT"), nullable=False
    )
    split: Mapped[str] = mapped_column(Text, nullable=False)
    probe_label_run_id: Mapped[str] = mapped_column(String(40), nullable=False)
    judge_label_run_id: Mapped[str] = mapped_column(String(40), nullable=False)
    #: ``{kind: "label_column", column} | {kind: "label_run", label_run_id}``.
    reference: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    figures: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    judge_is_training_labeler: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class LengthProfile(Base):
    __tablename__ = "dw_length_profiles"
    __table_args__ = (
        CheckConstraint(
            "unit IN (" + ", ".join(f"'{u}'" for u in LENGTH_UNITS) + ")", name="unit_valid"
        ),
        CheckConstraint("n >= 0", name="n_nonnegative"),
    )

    version_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_versions.id", ondelete="RESTRICT"), primary_key=True
    )
    split: Mapped[str] = mapped_column(Text, primary_key=True)
    column: Mapped[str] = mapped_column(Text, primary_key=True)
    unit: Mapped[str] = mapped_column(String(16), primary_key=True)
    measure_version: Mapped[str] = mapped_column(String(32), primary_key=True)
    n: Mapped[int] = mapped_column(Integer, nullable=False)
    #: ``p05, p25, p50, p75, p95, p99`` (FR-009.9).
    quantiles: Mapped[dict[str, float]] = mapped_column(JSONB, nullable=False)
    histogram: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    #: 1,001 per-mille quantile points: the distribution the overlap check reads (length.overlap).
    permille: Mapped[list[float]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
