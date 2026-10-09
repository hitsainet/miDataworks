"""``dw_model_leases`` and ``dw_model_lease_members``: the shared miLLM lease (X-08; FTDD §6.5).

miDataworks holds at most one live lease per miLLM server (one resident model per server, miLLM
FR-29.1.4), joined by every job on that model. The lease ID is encrypted at rest and decrypted
only in ``services/model_lease_holder.py``; it is never logged or returned (miLLM FR-29.1.6).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..core.database import Base

LEASE_STATES: tuple[str, ...] = ("active", "released", "lost")


class ModelLease(Base):
    __tablename__ = "dw_model_leases"
    __table_args__ = (
        CheckConstraint("state IN ('active', 'released', 'lost')", name="state_valid"),
        CheckConstraint("ttl_seconds BETWEEN 1 AND 7200", name="ttl_range"),
        Index(
            "uq_dw_model_leases_active_base_url",
            "base_url",
            unique=True,
            postgresql_where=text("state = 'active'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    #: The server origin (no trailing ``/v1``).
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    #: miLLM's integer model id: the ``{model_id}`` of the lease routes.
    millm_model_id: Mapped[int] = mapped_column(Integer, nullable=False)
    model_name: Mapped[str] = mapped_column(Text, nullable=False)
    lease_id_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    holder: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    ttl_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    lost_reason: Mapped[str | None] = mapped_column(Text)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ModelLeaseMember(Base):
    """A job (or a step's token) sharing a lease. ``left_at`` set when it leaves."""

    __tablename__ = "dw_model_lease_members"

    lease_row_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("dw_model_leases.id", ondelete="CASCADE"), primary_key=True
    )
    member_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
