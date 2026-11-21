"""``dw_operator_allowlist``: append-only allow/revoke history for entry-point operators (FR-003.11).

FTDD 003 section 4.1. Current state is the latest row per (distribution, distribution version,
entry point). A new distribution version is a new triple, so new code needs a new decision.

``origin`` is checked to equal ``'operator'``: agents cannot change the allowlist (P-09), and the
check makes that a schema fact rather than only a route rule. ``action`` uses a CHECK constraint,
like every other vocabulary column in this schema, rather than the PostgreSQL enum the FTID named
(recorded in the implementation controls record).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Identity, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ..core.database import Base

ALLOWLIST_ACTIONS: tuple[str, ...] = ("allow", "revoke")


class OperatorAllowlistEntry(Base):
    __tablename__ = "dw_operator_allowlist"
    __table_args__ = (
        CheckConstraint("action IN ('allow', 'revoke')", name="action_valid"),
        CheckConstraint("origin = 'operator'", name="origin_operator_only"),
        CheckConstraint("length(btrim(reason)) > 0", name="reason_present"),
        CheckConstraint("length(btrim(changed_by)) > 0", name="changed_by_present"),
        Index(
            "ix_dw_operator_allowlist_triple_created",
            "distribution",
            "distribution_version",
            "entry_point",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    distribution: Mapped[str] = mapped_column(String(255), nullable=False)
    distribution_version: Mapped[str] = mapped_column(String(128), nullable=False)
    entry_point: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    changed_by: Mapped[str] = mapped_column(String(255), nullable=False)
    origin: Mapped[str] = mapped_column(String(16), nullable=False, default="operator")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
