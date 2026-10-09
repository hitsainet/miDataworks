"""``dw_label_runs``, ``dw_label_run_jobs``, ``dw_label_run_chunks`` (FR-005.24 – FR-005.33).

A label run is one pass of one question (or rubric) over one input version. Its jobs live in
``dw_jobs``; a resume adds a job and never re-opens a terminal one (ADR-007). A chunk row is the
commit marker for a staged chunk file: a renamed file without its row is committed on recovery,
never rescored (FR-005.32).

``state`` is written only by ``services/label_run_service.py``. ``pinned`` and
``revision_reported`` are NULL until the worker knows them (P-13), never a guessed default.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
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

#: 009 adds ``probe_verdict`` and ``feature_tag`` (009 FTDD section 4.2; migration 0016).
RUN_KINDS: tuple[str, ...] = (
    "classifier",
    "judge",
    "rederived",
    "aggregate",
    "probe_verdict",
    "feature_tag",
)
RUN_STATES: tuple[str, ...] = (
    "awaiting_approval",
    "queued",
    "running",
    "cancelled",
    "completed",
    "failed",
    "rejected",
)
TERMINAL_RUN_STATES: frozenset[str] = frozenset({"cancelled", "completed", "failed", "rejected"})
#: A run in one of these may be resumed (FR-005.33): a new job continues it.
RESUMABLE_RUN_STATES: frozenset[str] = frozenset({"cancelled", "failed"})
STRUCTURED_MODES: tuple[str, ...] = ("json_schema", "strict_parse", "n/a")
PACKING_MODES: tuple[str, ...] = ("single", "packed", "batch")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class LabelRun(Base):
    __tablename__ = "dw_label_runs"
    __table_args__ = (
        CheckConstraint(_in("kind", RUN_KINDS), name="kind_valid"),
        CheckConstraint(_in("state", RUN_STATES), name="state_valid"),
        CheckConstraint(_in("structured_output", STRUCTURED_MODES), name="structured_valid"),
        CheckConstraint(_in("packing", PACKING_MODES), name="packing_valid"),
        CheckConstraint("started_by_origin IN ('operator', 'agent')", name="origin_valid"),
        CheckConstraint("length(started_by) > 0", name="started_by_present"),
        CheckConstraint(
            "threshold_positive IS NULL OR threshold_negative IS NULL OR "
            "(threshold_negative < threshold_positive AND threshold_negative >= 0 "
            "AND threshold_positive <= 1)",
            name="thresholds_ordered",
        ),
        CheckConstraint("chunk_size > 0", name="chunk_size_positive"),
        CheckConstraint(
            "rows_total >= 0 AND rows_reused >= 0 AND agent_counted_rows >= 0",
            name="row_counts_nonnegative",
        ),
        # 0021: a coverage record is an object or absent, never JSON null (0016's lesson).
        CheckConstraint(
            "row_coverage IS NULL OR COALESCE(jsonb_typeof(row_coverage), '') = 'object'",
            name="row_coverage_object",
        ),
        Index(
            "ix_dw_label_runs_version_origin_created",
            "input_version_id",
            "started_by_origin",
            "created_at",
        ),
        Index("ix_dw_label_runs_identity_hash", "labeler_identity_hash"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    input_version_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_versions.id", ondelete="RESTRICT"), nullable=False
    )
    #: Template input field -> version column.
    field_map: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    #: role, protocol, variant, base URL, model id, revision or null, server kind. NEVER a key.
    endpoint_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    template_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("dw_decision_templates.id", ondelete="RESTRICT")
    )
    rubric_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("dw_rubrics.id", ondelete="RESTRICT")
    )
    question: Mapped[str | None] = mapped_column(Text)
    positive_label: Mapped[str | None] = mapped_column(Text)
    negative_label: Mapped[str | None] = mapped_column(Text)
    threshold_positive: Mapped[float | None] = mapped_column(Float)
    threshold_negative: Mapped[float | None] = mapped_column(Float)
    min_top_probability: Mapped[float | None] = mapped_column(Float)
    label_set: Mapped[list[str] | None] = mapped_column(JSONB)
    #: temperature, seed, max tokens.
    sampling: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    structured_output: Mapped[str] = mapped_column(String(16), nullable=False, default="n/a")
    packing: Mapped[str] = mapped_column(String(16), nullable=False, default="single")
    batch_id: Mapped[str | None] = mapped_column(String(128))
    chunk_size: Mapped[int] = mapped_column(Integer, nullable=False)
    #: ``{"origin": "generated" | "source"}`` (FR-005.53), or null for every row.
    row_filter: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    parent_run_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    labeler_identity: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    labeler_identity_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    labeler_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    pinned: Mapped[bool | None] = mapped_column(Boolean)
    revision_reported: Mapped[bool | None] = mapped_column(Boolean)
    #: The first response's ``system_fingerprint`` (miLLM FR-25.13.8), verbatim; a later
    #: different one stops the run.
    system_fingerprint: Mapped[str | None] = mapped_column(Text)
    counts: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False, default=dict)
    keep_share_estimate: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    keep_share_actual: Mapped[float | None] = mapped_column(Float)
    length_correlation: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    rows_total: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    #: 0021 (2026-10-08 live finding 2): the rows the run covers beside ``rows_total``, the
    #: distinct row keys it scores (``rows``, ``row_keys``, ``keys_with_copies``, ...). NULL on a
    #: run started before 0021: not recorded, never back-filled with a guess. ``none_as_null``: a
    #: Python None is SQL NULL, not JSON ``null`` (which the CHECK refuses; found by control R6).
    row_coverage: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    rows_reused: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    agent_counted_rows: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    approval_id: Mapped[str | None] = mapped_column(String(40))
    #: ``{code, message}`` of the last failure; cleared on resume.
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    started_by: Mapped[str] = mapped_column(Text, nullable=False)
    started_by_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LabelRunJob(Base):
    """One job of a run, in order. A resume adds the next ``seq``."""

    __tablename__ = "dw_label_run_jobs"
    __table_args__ = (UniqueConstraint("job_id", name="uq_dw_label_run_jobs_job_id"),)

    label_run_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("dw_label_runs.id", ondelete="CASCADE"), primary_key=True
    )
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("dw_jobs.id", ondelete="RESTRICT"), nullable=False
    )


class LabelRunChunk(Base):
    """The commit marker of one staged chunk file (FR-005.32)."""

    __tablename__ = "dw_label_run_chunks"
    __table_args__ = (CheckConstraint("row_count >= 0", name="row_count_nonnegative"),)

    label_run_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("dw_label_runs.id", ondelete="CASCADE"), primary_key=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(String(40), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Relative to ``DATA_DIR``.
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    committed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
