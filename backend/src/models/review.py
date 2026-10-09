"""``dw_review_queues``, ``dw_review_items``, ``dw_review_decisions``, ``dw_audits``
(FR-006.22 – FR-006.31; FTDD 006 section 4.2).

Decisions are APPEND-ONLY: a database trigger (``dw_append_only``, migration 0014) refuses every
``UPDATE`` and ``DELETE``, and no service has an update path. The effective label is computed
from the history by one resolver (``services/review/effective_label.py``).

Identifiers follow this repository's convention (``rq_…``, ``ri_…``, ``rd_…``, ``au_…``,
``String(40)``) rather than FTDD 006's UUIDs; versions keep their UUID key.
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
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..core.database import Base
from .enums import AuditState, ReviewDecisionKind, ReviewQueueKind, ReviewQueueState, check_in

_ORIGIN = "IN ('operator', 'agent')"


class ReviewQueue(Base):
    __tablename__ = "dw_review_queues"
    __table_args__ = (
        CheckConstraint(check_in("kind", ReviewQueueKind), name="kind_valid"),
        CheckConstraint(check_in("state", ReviewQueueState), name="state_valid"),
        CheckConstraint(f"created_by_origin {_ORIGIN}", name="origin_valid"),
        CheckConstraint("length(created_by) > 0", name="created_by_present"),
        CheckConstraint(
            "(kind = 'external') = (version_id IS NULL)", name="version_unless_external"
        ),
        CheckConstraint(
            "kind <> 'label_review' OR label_run_id IS NOT NULL", name="review_has_run"
        ),
        CheckConstraint("kind <> 'external' OR origin_app IS NOT NULL", name="external_has_app"),
        CheckConstraint("question_hash ~ '^[0-9a-f]{64}$'", name="question_hash_pattern"),
        Index("ix_dw_review_queues_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    version_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_versions.id", ondelete="RESTRICT")
    )
    label_run_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("dw_label_runs.id", ondelete="RESTRICT")
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    question_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    label_set: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    show_model_output: Mapped[bool] = mapped_column(Boolean, nullable=False)
    #: Seed, strata, size and source (row keys or a sample).
    sample_spec: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    #: The calling application for external queues, from the agent header (FTDD 006 section 5.4).
    origin_app: Mapped[str | None] = mapped_column(Text)
    external_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ReviewItem(Base):
    """A version row (``row_key``) or an external candidate (``external_id``), never both."""

    __tablename__ = "dw_review_items"
    __table_args__ = (
        CheckConstraint("(row_key IS NULL) <> (external_id IS NULL)", name="row_key_xor_external"),
        CheckConstraint("row_key IS NULL OR row_key ~ '^[0-9a-f]{64}$'", name="row_key_pattern"),
        UniqueConstraint("queue_id", "row_key", name="uq_dw_review_items_queue_row_key"),
        UniqueConstraint("queue_id", "external_id", name="uq_dw_review_items_queue_external_id"),
        UniqueConstraint("queue_id", "position", name="uq_dw_review_items_queue_position"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    queue_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("dw_review_queues.id", ondelete="RESTRICT"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    row_key: Mapped[str | None] = mapped_column(String(64))
    external_id: Mapped[str | None] = mapped_column(Text)
    #: External text (prompt, completion, model output, provenance); stored as data, never HTML.
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    #: Outcome, probability, rationale and label run at queueing time.
    model_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    stratum: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ReviewDecision(Base):
    """One decision. Never updated or deleted (trigger ``dw_append_only``)."""

    __tablename__ = "dw_review_decisions"
    __table_args__ = (
        CheckConstraint(check_in("decision", ReviewDecisionKind), name="decision_valid"),
        CheckConstraint(
            "(decision = 'override') = (override_label IS NOT NULL)", name="override_label_iff"
        ),
        CheckConstraint(f"decided_by_origin {_ORIGIN}", name="origin_valid"),
        CheckConstraint("length(decided_by) > 0", name="decided_by_present"),
        CheckConstraint("length(reason) > 0", name="reason_present"),
        CheckConstraint("question_hash ~ '^[0-9a-f]{64}$'", name="question_hash_pattern"),
        Index("ix_dw_review_decisions_row_key_question", "row_key", "question_hash", "created_at"),
        Index("ix_dw_review_decisions_queue_created", "queue_id", "created_at"),
        Index("ix_dw_review_decisions_item_created", "item_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    item_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("dw_review_items.id", ondelete="RESTRICT"), nullable=False
    )
    queue_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("dw_review_queues.id", ondelete="RESTRICT"), nullable=False
    )
    row_key: Mapped[str | None] = mapped_column(String(64))
    question_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    version_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_versions.id", ondelete="RESTRICT")
    )
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    override_label: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    model_output_visible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    decided_by: Mapped[str] = mapped_column(Text, nullable=False)
    decided_by_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    #: clock_timestamp(), not now(): two decisions in one transaction still order.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )


class Audit(Base):
    """A version's stratified audit sample (FR-006.27 – FR-006.29). At most one in progress."""

    __tablename__ = "dw_audits"
    __table_args__ = (
        CheckConstraint(check_in("state", AuditState), name="state_valid"),
        CheckConstraint("size BETWEEN 50 AND 100", name="size_in_range"),
        CheckConstraint(f"created_by_origin {_ORIGIN}", name="origin_valid"),
        CheckConstraint("length(created_by) > 0", name="created_by_present"),
        Index(
            "uq_dw_audits_one_in_progress",
            "version_id",
            unique=True,
            postgresql_where=text("state = 'in_progress'"),
        ),
        Index("ix_dw_audits_version_created", "version_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    version_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_versions.id", ondelete="RESTRICT"), nullable=False
    )
    queue_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("dw_review_queues.id", ondelete="RESTRICT"), nullable=False
    )
    question_hash: Mapped[str | None] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    strata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="in_progress")
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
