"""Archive (operator decision 2026-10-20; migration ``0030``).

:class:`Archivable` adds the four archive columns to datasets, label runs, generation runs and
approvals. Archiving hides an item from default lists and pickers; it deletes nothing and touches
no other column, so everything that points at it still reads. :class:`ArchiveEvent` is the
append-only trail of every archive and unarchive.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ..core.database import Base

#: entity kind -> what the operator calls it.
ARCHIVE_KINDS: dict[str, str] = {
    "dataset": "dataset",
    "label_run": "label run",
    "generation_run": "generation run",
    "approval": "approval",
    "rule_set": "rule set",  # feature 016 (migration 0036)
    "code_rule": "code rule",  # feature 018 (migration 0043)
}
ARCHIVE_ACTIONS: tuple[str, ...] = ("archive", "unarchive")

ARCHIVED_CONSISTENT = (
    "(archived_at IS NULL AND archived_by IS NULL AND archived_by_origin IS NULL "
    "AND archive_reason IS NULL) OR "
    "(archived_at IS NOT NULL AND archived_by IS NOT NULL AND length(archived_by) > 0 "
    "AND archived_by_origin IN ('operator', 'agent'))"
)


def archived_check() -> CheckConstraint:
    """The table-arg every archivable table carries (``ck_<table>_archived_consistent``)."""
    return CheckConstraint(ARCHIVED_CONSISTENT, name="archived_consistent")


class Archivable:
    """Mixin: NULL in all four columns means shown."""

    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_by: Mapped[str | None] = mapped_column(Text)
    archived_by_origin: Mapped[str | None] = mapped_column(String(16))
    archive_reason: Mapped[str | None] = mapped_column(Text)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class ArchiveEvent(Base):
    __tablename__ = "dw_archive_events"
    __table_args__ = (
        CheckConstraint(_in("entity_kind", tuple(ARCHIVE_KINDS)), name="entity_kind_valid"),
        CheckConstraint(_in("action", ARCHIVE_ACTIONS), name="action_valid"),
        CheckConstraint(
            "actor_origin IN ('operator', 'agent') AND length(actor) > 0", name="actor_valid"
        ),
        Index("ix_dw_archive_events_entity", "entity_kind", "entity_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    entity_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    actor_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
