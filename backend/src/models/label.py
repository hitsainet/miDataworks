"""``dw_labels``: one label per (label run, row key) (FR-005.24, FR-005.29; P-20).

Provenance that is the same for every row (endpoint, model, revision, template, question,
thresholds, who) lives on the run and is reached through it; a reader never returns a label
without its run. A fact the endpoint did not report reads "not reported", never a default.
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
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..core.database import Base

#: The fixed outcomes (FR-005.18, FR-005.41, FR-005.43, FR-005.47). A categorical run's label
#: names are outcomes too (FR-005.22); the service checks those against the run's label set.
FIXED_OUTCOMES: tuple[str, ...] = (
    "positive",
    "negative",
    "excluded",
    "skipped",
    "parse_failure",
    "position_inconsistent",
)


class Label(Base):
    __tablename__ = "dw_labels"
    __table_args__ = (
        CheckConstraint("length(outcome) BETWEEN 1 AND 128", name="outcome_present"),
        CheckConstraint("row_key ~ '^[0-9a-f]{64}$'", name="row_key_pattern"),
        CheckConstraint(
            "probability IS NULL OR (probability >= 0 AND probability <= 1)",
            name="probability_range",
        ),
        Index("ix_dw_labels_fingerprint_row_key", "labeler_fingerprint", "row_key"),
    )

    label_run_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("dw_label_runs.id", ondelete="CASCADE"), primary_key=True
    )
    row_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    labeler_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(128), nullable=False)
    parsed_value: Mapped[Any] = mapped_column(JSONB)
    probability: Mapped[float | None] = mapped_column(Float)
    distribution: Mapped[dict[str, float] | None] = mapped_column(JSONB)
    #: Verbatim response fragment; NULL for re-derived rows (read through the parent).
    raw_output: Mapped[Any] = mapped_column(JSONB)
    rationale: Mapped[str | None] = mapped_column(Text)
    steering_state: Mapped[str] = mapped_column(Text, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    skip_reason: Mapped[str | None] = mapped_column(Text)
    provisional: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    reused_from_run_id: Mapped[str | None] = mapped_column(String(40))
    chunk_index: Mapped[int | None] = mapped_column(Integer)
    scored_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
