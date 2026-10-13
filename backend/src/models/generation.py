"""Feature 007's tables: templates, runs, snapshots, chunks, records, pairs, diversity reports.

FTDD 007 section 4.1. **The generated text is not in any of these tables**: it lives in the chunk
Parquet under ``runs/<run_id>/gen/`` (ADR-003, R-03.10).

Immutable after insert (migration ``0017``, the shared ``dw_append_only`` trigger): steering
snapshots, generation records, generation pairs and diversity reports. A template refuses an
UPDATE once ``used_at`` is set (``TEMPLATE_IMMUTABLE``, as 005's templates do).

``GenerationRun.state`` is written only by ``services/generation/run_service.py`` (API side) and
the engine through ``run_service.set_state`` (worker side). ``pinned`` and ``revision_reported``
are NULL until the worker knows them (P-13), never a guessed default.

Deviation from FTDD 007 section 4.1 (recorded in the implementation controls): records are keyed
``(run_id, stage, record_index)`` — the expansion and response stages each number their records
from 0, so one run's two stages never collide on an index.
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
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..core.database import Base

#: ``minimal_pairs``: 009 FR-009.60 - one minimal edit per seed row (operator decision 2026-10-07).
RUN_MODES: tuple[str, ...] = ("standard", "steered_pairs", "minimal_pairs")
RUN_STATES: tuple[str, ...] = ("planned", "queued", "running", "completed", "cancelled", "failed")
TERMINAL_RUN_STATES: frozenset[str] = frozenset({"completed", "cancelled", "failed"})
RESUMABLE_RUN_STATES: frozenset[str] = frozenset({"cancelled", "failed"})
#: Failures a resume may not continue: the setting itself moved (FR-007.17).
NOT_RESUMABLE_REASONS: frozenset[str] = frozenset({"profile_changed"})
#: ``detector``: only a ``minimal_pairs`` run writes it (009 FR-009.60).
TARGET_TYPES: tuple[str, ...] = ("sft", "kto", "grpo_prompt", "dpo", "detector")
TEMPLATE_KINDS: tuple[str, ...] = ("expand", "respond")
STAGES: tuple[str, ...] = ("expand", "respond")
SIDES: tuple[str, ...] = ("generator", "a", "b")
SETTING_KINDS: tuple[str, ...] = ("none", "profile", "inline")
CHECK_RESULTS: tuple[str, ...] = ("match", "mismatch", "unreported", "not_applicable")
OUTCOMES: tuple[str, ...] = ("generated", "discarded", "skipped")
REASON_CODES: tuple[str, ...] = (
    "steering_mismatch",
    "steering_unreported",
    "parse_failure",
    "context_overflow",
    "profile_changed",
    "pair_partner_discarded",
    "row_error",
    "empty_response",
)
VERDICTS: tuple[str, ...] = ("holds", "falls", "not_measured", "invalid")
ENGINE_PATHS: tuple[str, ...] = ("relay", "native")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class GenerationTemplate(Base):
    __tablename__ = "dw_generation_templates"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_dw_generation_templates_name_version"),
        CheckConstraint(_in("kind", TEMPLATE_KINDS), name="kind_valid"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "created_by_origin IN ('operator', 'agent', 'system')", name="origin_valid"
        ),
        CheckConstraint("length(created_by) > 0", name="created_by_present"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="content_hash_pattern"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    body: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cloned_from: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("dw_generation_templates.id", ondelete="RESTRICT")
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GenerationRun(Base):
    __tablename__ = "dw_generation_runs"
    __table_args__ = (
        CheckConstraint(_in("mode", RUN_MODES), name="mode_valid"),
        CheckConstraint(_in("state", RUN_STATES), name="state_valid"),
        CheckConstraint(_in("target_type", TARGET_TYPES), name="target_valid"),
        CheckConstraint(_in("engine_path", ENGINE_PATHS), name="engine_path_valid"),
        CheckConstraint("n_responses BETWEEN 1 AND 16", name="n_responses_range"),
        CheckConstraint("sample_size BETWEEN 1 AND 100000", name="sample_size_range"),
        CheckConstraint(
            "mode <> 'steered_pairs' OR (n_responses = 1 AND chosen_side IN ('a', 'b'))",
            name="steered_shape",
        ),
        CheckConstraint(
            "mode <> 'standard' OR chosen_side IS NULL", name="standard_has_no_chosen_side"
        ),
        CheckConstraint(
            "mode <> 'minimal_pairs' OR (n_responses = 1 AND chosen_side IS NULL "
            "AND target_type = 'detector')",
            name="minimal_pairs_shape",
        ),
        CheckConstraint(
            "target_type <> 'detector' OR mode = 'minimal_pairs'", name="detector_is_minimal_pairs"
        ),
        CheckConstraint("started_by_origin IN ('operator', 'agent')", name="origin_valid"),
        CheckConstraint("length(started_by) > 0", name="started_by_present"),
        Index("ix_dw_generation_runs_version_created", "input_version_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    input_version_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_versions.id", ondelete="RESTRICT"), nullable=False
    )
    held_out_origin_version_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_versions.id", ondelete="RESTRICT"), nullable=False
    )
    held_out_splits: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    prompt_column: Mapped[str] = mapped_column(Text, nullable=False)
    seed_splits: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    n_responses: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    expand_template_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("dw_generation_templates.id", ondelete="RESTRICT")
    )
    respond_template_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("dw_generation_templates.id", ondelete="RESTRICT")
    )
    stages: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    #: role, protocol, base URL, model ID, inherited_from — NEVER a key.
    generation_endpoint: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    server_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    generator_identities: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    judge_identity: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    judge_identity_hash: Mapped[str | None] = mapped_column(String(64))
    independence_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    chosen_side: Mapped[str | None] = mapped_column(String(1))
    engine_path: Mapped[str] = mapped_column(String(8), nullable=False)
    steering_supported: Mapped[bool] = mapped_column(Boolean, nullable=False)
    pinned: Mapped[bool | None] = mapped_column(Boolean)
    revision_reported: Mapped[bool | None] = mapped_column(Boolean)
    model_revision: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(String(32))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    warnings: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    started_by: Mapped[str] = mapped_column(Text, nullable=False)
    started_by_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GenerationRunJob(Base):
    __tablename__ = "dw_generation_run_jobs"
    __table_args__ = (Index("ix_dw_generation_run_jobs_job", "job_id", unique=True),)

    run_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("dw_generation_runs.id", ondelete="RESTRICT"), primary_key=True
    )
    seq: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    job_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("dw_jobs.id", ondelete="RESTRICT"), nullable=False
    )


class SteeringSnapshot(Base):
    __tablename__ = "dw_steering_snapshots"
    __table_args__ = (
        CheckConstraint(_in("side", SIDES), name="side_valid"),
        CheckConstraint(_in("kind", SETTING_KINDS), name="kind_valid"),
        CheckConstraint(
            "(kind = 'none' AND set_hash IS NULL) OR "
            "(kind <> 'none' AND set_hash ~ '^sha256:[0-9a-f]{64}$')",
            name="set_hash_shape",
        ),
    )

    run_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("dw_generation_runs.id", ondelete="RESTRICT"), primary_key=True
    )
    side: Mapped[str] = mapped_column(String(16), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    profile_id: Mapped[str | None] = mapped_column(Text)
    profile_name: Mapped[str | None] = mapped_column(Text)
    #: miLLM's ``updated_at`` exactly as served (compared as text before each chunk).
    profile_updated_at: Mapped[str | None] = mapped_column(Text)
    intensity: Mapped[float | None] = mapped_column(Float)
    model_id: Mapped[str | None] = mapped_column(Text)
    sae_id: Mapped[str | None] = mapped_column(Text)
    layer: Mapped[int | None] = mapped_column(Integer)
    #: Sorted ``[[index, applied strength], ...]`` (after miLLM's clamp, zeros removed).
    features: Mapped[list[list[float]]] = mapped_column(JSONB, nullable=False)
    #: The strengths sent, ``[[index, strength], ...]`` (equal to ``features`` unless clamped).
    sent_features: Mapped[list[list[float]]] = mapped_column(JSONB, nullable=False)
    set_hash: Mapped[str | None] = mapped_column(String(71))
    body_overrides: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class GenerationChunk(Base):
    __tablename__ = "dw_generation_chunks"
    __table_args__ = (CheckConstraint(_in("stage", STAGES), name="stage_valid"),)

    run_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("dw_generation_runs.id", ondelete="RESTRICT"), primary_key=True
    )
    stage: Mapped[str] = mapped_column(String(8), primary_key=True)
    chunk_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("dw_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    committed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GenerationRecord(Base):
    __tablename__ = "dw_generation_records"
    __table_args__ = (
        CheckConstraint(_in("stage", STAGES), name="stage_valid"),
        CheckConstraint(_in("steering_check", CHECK_RESULTS), name="check_valid"),
        CheckConstraint(_in("outcome", OUTCOMES), name="outcome_valid"),
        CheckConstraint("side IS NULL OR side IN ('a', 'b')", name="side_valid"),
        CheckConstraint(
            "(outcome = 'generated' AND reason_code IS NULL) OR "
            "(outcome <> 'generated' AND reason_code IS NOT NULL)",
            name="reason_when_not_generated",
        ),
        CheckConstraint(
            "outcome <> 'generated' OR steering_check IN ('match', 'not_applicable') OR "
            "(steering_check = 'unreported' AND requested_set_hash IS NULL)",
            name="generated_only_when_checked",
        ),
        Index("ix_dw_generation_records_run_outcome", "run_id", "outcome"),
        Index("ix_dw_generation_records_row_key", "row_key"),
    )

    run_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("dw_generation_runs.id", ondelete="RESTRICT"), primary_key=True
    )
    stage: Mapped[str] = mapped_column(String(8), primary_key=True)
    record_index: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    seed_position: Mapped[int] = mapped_column(Integer, nullable=False)
    row_key: Mapped[str | None] = mapped_column(String(64))
    seed_row_key: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_row_key: Mapped[str] = mapped_column(String(64), nullable=False)
    response_index: Mapped[int] = mapped_column(Integer, nullable=False)
    side: Mapped[str | None] = mapped_column(String(1))
    template_id: Mapped[str | None] = mapped_column(String(40))
    model_id: Mapped[str | None] = mapped_column(Text)
    model_revision: Mapped[str | None] = mapped_column(Text)
    requested_set_hash: Mapped[str | None] = mapped_column(String(71))
    #: ``X-miLLM-Steering`` verbatim; NULL when the header was absent (never "unsteered").
    reported_steering: Mapped[str | None] = mapped_column(Text)
    steering_check: Mapped[str] = mapped_column(String(16), nullable=False)
    check_reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    seed_sent: Mapped[int | None] = mapped_column(BigInteger)
    #: True/False when miLLM echoed X-miLLM-Seed; NULL when it did not (seed not reported).
    seed_confirmed: Mapped[bool | None] = mapped_column(Boolean)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    finish_reason: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(32))
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)


class GenerationPair(Base):
    __tablename__ = "dw_generation_pairs"

    run_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("dw_generation_runs.id", ondelete="RESTRICT"), primary_key=True
    )
    prompt_row_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    pair_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    record_index_a: Mapped[int] = mapped_column(BigInteger, nullable=False)
    record_index_b: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chosen_side: Mapped[str] = mapped_column(String(1), nullable=False)
    shared_seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)


class DiversityReport(Base):
    __tablename__ = "dw_diversity_reports"
    __table_args__ = (
        UniqueConstraint(
            "version_id", "column", "method_hash", name="uq_dw_diversity_reports_identity"
        ),
        CheckConstraint(_in("verdict", VERDICTS), name="verdict_valid"),
        CheckConstraint(
            "created_by_origin IN ('operator', 'agent', 'system')", name="origin_valid"
        ),
        Index("ix_dw_diversity_reports_version", "version_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    version_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_versions.id", ondelete="RESTRICT"), nullable=False
    )
    reference_version_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_versions.id", ondelete="RESTRICT")
    )
    column: Mapped[str] = mapped_column(Text, nullable=False)
    method_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    method: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    splits: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    embedding_identity: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    clustering: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    figures: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    job_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("dw_jobs.id", ondelete="RESTRICT")
    )
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
