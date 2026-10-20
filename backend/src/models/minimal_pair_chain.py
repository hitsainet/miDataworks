"""A minimal-pair chain: the four stages that make 009's minimal pairs (FR-009.60 - FR-009.64).

Operator decision 2026-10-07: the generator is a chain of pieces that already exist, each recording
its own provenance. This row records only which piece each stage is, so the operator can see each
stage and resume the one that stopped:

``generate`` (a 007 generation run) → ``scope`` (a 002 build: the counterparts and their seeds) →
``judge`` (a 005 judge label run, pinned rubric) → ``pair`` (a 002 build: verified flips with
``pair_id``) → ``done``.

``state`` is ``running`` while a stage is live or about to start, ``failed`` when a stage failed or
refused to start (``failed_stage`` names it, ``error`` says why), ``cancelled`` by the operator, and
``completed`` with ``pair_version_id`` set. Only ``services/detector_sets/minimal_pair_chain.py``
writes it.
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
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..core.database import Base

CHAIN_STATES: tuple[str, ...] = ("running", "completed", "failed", "cancelled")
CHAIN_STAGES: tuple[str, ...] = ("generate", "scope", "judge", "pair", "done")
RESUMABLE_CHAIN_STATES: frozenset[str] = frozenset({"failed", "cancelled"})


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class MinimalPairChain(Base):
    __tablename__ = "dw_minimal_pair_chains"
    __table_args__ = (
        CheckConstraint(_in("state", CHAIN_STATES), name="state_valid"),
        CheckConstraint(_in("stage", CHAIN_STAGES), name="stage_valid"),
        CheckConstraint(
            "failed_stage IS NULL OR " + _in("failed_stage", CHAIN_STAGES[:4]),
            name="failed_stage_valid",
        ),
        CheckConstraint("state <> 'failed' OR failed_stage IS NOT NULL", name="failure_named"),
        CheckConstraint(
            "state <> 'completed' OR (stage = 'done' AND pair_version_id IS NOT NULL)",
            name="completed_has_pairs",
        ),
        CheckConstraint("started_by_origin IN ('operator', 'agent')", name="origin_valid"),
        CheckConstraint("acting_origin IN ('operator', 'agent')", name="acting_origin_valid"),
        CheckConstraint("length(started_by) > 0", name="started_by_present"),
        Index("ix_dw_minimal_pair_chains_state", "state", "updated_at"),
        Index("ix_dw_minimal_pair_chains_version", "input_version_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    input_version_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_versions.id", ondelete="RESTRICT"), nullable=False
    )
    #: The chain request as accepted (``MinimalPairChainCreate``), written once.
    request: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    generation_run_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("dw_generation_runs.id", ondelete="RESTRICT"), nullable=False
    )
    scope_job_id: Mapped[str | None] = mapped_column(String(40))
    scope_version_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_versions.id", ondelete="SET NULL")
    )
    judge_run_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("dw_label_runs.id", ondelete="RESTRICT")
    )
    pair_job_id: Mapped[str | None] = mapped_column(String(40))
    pair_version_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_versions.id", ondelete="SET NULL")
    )
    failed_stage: Mapped[str | None] = mapped_column(String(16))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    #: The pairing step's report: pairs in, verified, dropped by reason (FR-009.61).
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    started_by: Mapped[str] = mapped_column(Text, nullable=False)
    started_by_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Who later stages start as: the starter, or whoever last resumed the chain.
    acting_by: Mapped[str] = mapped_column(Text, nullable=False)
    acting_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
