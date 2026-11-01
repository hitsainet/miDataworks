"""``dw_agent_label_rows``: the P-07 ledger of agent labelling rows (010 FTDD section 4.3).

Feature 010 owns this table and its window predicate. It lands with feature 002 because 002's build
routes carry the ``agent_label_rows`` gate (FR-002.50, confirmed S3-02) and FTASKS 9.11 requires
the ledger to record ``probe_verdict`` rows.

One departure from 010 FTDD section 4.3: ``version_id`` carries no foreign key to ``dw_versions``.
A build labels rows before its version exists, and a build whose inputs are sources only has no
version at all, so the ledger is keyed on the build's *scope*: its first input version, else its
first source (``services/agent_label_ledger.py::build_scope``). P-07's "same version" then reads
"same data", which is what the window exists to bound.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from ..core.database import Base

RUN_KINDS: tuple[str, ...] = ("label_run", "feature_tagging", "probe_verdict")


class AgentLabelRow(Base):
    __tablename__ = "dw_agent_label_rows"
    __table_args__ = (
        CheckConstraint(
            "run_kind IN (" + ", ".join(f"'{k}'" for k in RUN_KINDS) + ")", name="run_kind_valid"
        ),
        CheckConstraint("rows_counted >= 0", name="rows_nonnegative"),
        CheckConstraint("length(identity) > 0", name="identity_present"),
        Index("ix_dw_agent_label_rows_version_admitted", "version_id", "admitted_at"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    version_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    identity: Mapped[str] = mapped_column(Text, nullable=False)
    run_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    rows_counted: Mapped[int] = mapped_column(Integer, nullable=False)
    admitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
