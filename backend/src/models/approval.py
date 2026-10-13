# Origin (shape): miStudio (Onegaishimas/miStudio) backend/src/models/agent_approval.py @ c829a2cc
# Mode: adapt (docs/REUSE.md). The columns follow 010 FTDD section 4.1, so feature 010 extends
# behaviour without a schema change: request digest, expiry, a status set that the janitor
# actually reaches (miStudio's `expired` was set by nothing), and an encrypted secret payload.
"""``dw_approvals``: agent-originated gated actions waiting for the operator (ADR-013; task 9.1)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..core.database import Base

APPROVAL_STATUSES: tuple[str, ...] = (
    "pending",
    "executing",
    "executed",
    "failed",
    "rejected",
    "expired",
)


class Approval(Base):
    __tablename__ = "dw_approvals"
    __table_args__ = (
        CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in APPROVAL_STATUSES) + ")",
            name="status_valid",
        ),
        CheckConstraint("length(requested_by) > 0", name="requested_by_present"),
        Index("ix_dw_approvals_status_expires", "status", "expires_at"),
        Index("ix_dw_approvals_requested_by_created", "requested_by", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    #: The route the action came through (method and template), for the banner.
    target: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: The validated request, secret fields replaced by their HMAC.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    #: sha256 of the canonical JSON of ``payload``.
    request_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    #: AES-GCM envelope of the secret fields, only for ``secret_write``; cleared once decided.
    secret_payload: Mapped[str | None] = mapped_column(Text)
    requested_by: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_by: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(Text)
    result_kind: Mapped[str | None] = mapped_column(String(32))
    result_id: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
