"""Purge tombstones (operator decision 2026-10-20; migration ``0032``).

A purge permanently deletes an archived dataset, label run, generation run or approval and what
belongs only to it (``services/purge_service.py``). :class:`PurgeEvent` is what stays: one
append-only row per purge naming the item, who purged it and why, and the row counts and bytes
removed. Only the operator purges, and the CHECK refuses any other origin.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..core.database import Base
from .archive import ARCHIVE_KINDS

#: What can be purged: exactly what can be archived.
PURGE_KINDS: dict[str, str] = dict(ARCHIVE_KINDS)


class PurgeEvent(Base):
    __tablename__ = "dw_purge_events"
    __table_args__ = (
        CheckConstraint(
            "entity_kind IN (" + ", ".join(f"'{k}'" for k in PURGE_KINDS) + ")",
            name="entity_kind_valid",
        ),
        CheckConstraint(
            "purged_by_origin = 'operator' AND length(purged_by) > 0", name="operator_only"
        ),
        CheckConstraint("bytes_removed >= 0 AND paths_removed >= 0", name="sizes_valid"),
        Index("ix_dw_purge_events_entity", "entity_kind", "entity_id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    entity_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    purged_by: Mapped[str] = mapped_column(Text, nullable=False)
    purged_by_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    bytes_removed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    paths_removed: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
