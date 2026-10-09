"""``dw_rubrics``: versioned, hashed judge rubrics (FR-005.16, FR-005.17).

Same shape as a decision template, with a style. Immutable once any run used it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..core.database import Base

RUBRIC_STYLES: tuple[str, ...] = ("pointwise", "pairwise", "binary", "stepwise")


class Rubric(Base):
    __tablename__ = "dw_rubrics"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_dw_rubrics_name_version"),
        UniqueConstraint("content_hash", name="uq_dw_rubrics_content_hash"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="content_hash_pattern"),
        CheckConstraint(
            "style IN ('pointwise', 'pairwise', 'binary', 'stepwise')", name="style_valid"
        ),
        CheckConstraint(
            "created_by_origin IN ('operator', 'agent', 'system')", name="origin_valid"
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    body: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    style: Mapped[str] = mapped_column(String(16), nullable=False)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
