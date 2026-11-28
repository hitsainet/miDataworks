"""``dw_row_events``: one row per dropped, changed, added or split-assigned row (FR-002.24).

Partitioned by HASH of ``step_execution_id`` into 32 partitions (T-09; PADR v1.1 ADR-003), so a
reused step's events are never copied: events belong to step executions, and a version reaches
them through ``dw_version_steps``. The partitions and indexes are created in raw SQL by migration
``0007``; this mapping is for reads. Inserts go through ``COPY`` in ``services/step_ingest.py``.

Hashes are ``BYTEA`` (32 bytes) here to halve index size; the API always shows lowercase hex.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import (
    ARRAY,
    BigInteger,
    CheckConstraint,
    Float,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..core.database import Base
from .enums import EventKind, check_in

PARTITIONS = 32
_PARTITION = re.compile(r"dw_row_events_p\d{2}")


class RowEvent(Base):
    __tablename__ = "dw_row_events"
    __table_args__ = (
        CheckConstraint(check_in("kind", EventKind), name="kind_valid"),
        CheckConstraint("octet_length(row_key) = 32", name="row_key_length"),
        CheckConstraint(
            "kind NOT IN ('dropped', 'changed') OR statistic_name IS NOT NULL",
            name="statistic_required",
        ),
        CheckConstraint("length(reason_code) > 0 AND length(reason) > 0", name="reason_present"),
        CheckConstraint(
            "(kind = 'changed') = (new_row_key IS NOT NULL)", name="new_key_only_when_changed"
        ),
        Index("ix_dw_row_events_row_key", "row_key"),
        Index(
            "ix_dw_row_events_new_row_key",
            "new_row_key",
            postgresql_where=text("new_row_key IS NOT NULL"),
        ),
        Index("ix_dw_row_events_exec_kind_reason", "step_execution_id", "kind", "reason_code"),
        {"postgresql_partition_by": "HASH (step_execution_id)"},
    )

    step_execution_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    row_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    occurrence: Mapped[int] = mapped_column(Integer, nullable=False)
    new_row_key: Mapped[bytes | None] = mapped_column(LargeBinary)
    new_occurrence: Mapped[int | None] = mapped_column(Integer)
    related_row_key: Mapped[bytes | None] = mapped_column(LargeBinary)
    parent_keys: Mapped[list[bytes] | None] = mapped_column(ARRAY(LargeBinary))
    split: Mapped[str | None] = mapped_column(String(64))
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    statistic_name: Mapped[str | None] = mapped_column(String(64))
    statistic_value: Mapped[float | None] = mapped_column(Float)
    statistic_text: Mapped[str | None] = mapped_column(Text)
    threshold: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


def is_partition_name(name: str | None) -> bool:
    """``dw_row_events_p00`` … ``p31``: created by migration 0007, not mapped by the ORM.

    Alembic's comparison and autogenerate skip these (``alembic/env.py`` and the ORM-versus-
    migration test both call this), and ``tests/integration/test_version_guards.py`` asserts
    all 32 exist.
    """
    return name is not None and _PARTITION.fullmatch(name) is not None
