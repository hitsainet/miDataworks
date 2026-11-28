"""``dw_text_separability``: the cached D-9 "wording alone" figure (015 FR-015.15 - FR-015.17).

One row per (version, split, columns, label mapping, pair column, sample size, measure version). A
version split is immutable, so a row is never edited: a change to any key part is a cache miss, and
``MEASURE_VERSION`` changes when the method changes. ``auroc`` is NULL when the figure could not be
computed (``reason``, e.g. ``too_few_rows``), which is never read as green.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..core.database import Base


class TextSeparability(Base):
    __tablename__ = "dw_text_separability"
    __table_args__ = (
        CheckConstraint("n >= 0", name="n_nonnegative"),
        CheckConstraint("auroc IS NULL OR (auroc >= 0 AND auroc <= 1)", name="auroc_range"),
    )

    version_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_versions.id", ondelete="RESTRICT"), primary_key=True
    )
    split: Mapped[str] = mapped_column(Text, primary_key=True)
    input_column: Mapped[str] = mapped_column(Text, primary_key=True)
    label_column: Mapped[str] = mapped_column(Text, primary_key=True)
    mapping_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    #: ``""`` when the role has no pair column (a primary-key part cannot be NULL).
    pair_column: Mapped[str] = mapped_column(Text, primary_key=True)
    #: The subsample size asked for (D55 compares at equal size).
    sample_n: Mapped[int] = mapped_column(Integer, primary_key=True)
    measure_version: Mapped[str] = mapped_column(String(32), primary_key=True)
    n: Mapped[int] = mapped_column(Integer, nullable=False)
    n_positive: Mapped[int] = mapped_column(Integer, nullable=False)
    n_negative: Mapped[int] = mapped_column(Integer, nullable=False)
    auroc: Mapped[float | None] = mapped_column(Float)
    folds: Mapped[int] = mapped_column(Integer, nullable=False)
    grouped: Mapped[bool] = mapped_column(Boolean, nullable=False)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
