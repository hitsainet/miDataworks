"""``dw_setting_changes``: the append-only history of settings changes (feature 012; ENH-003 D12).

One row per non-secret write or reset through ``PUT``/``DELETE /api/v1/settings/{key}``: the key,
the stored value before and after (``None`` = no override, the environment value applies), who
(``actor``: the operator's name or ``agent:<product>``), the approval that carried an agent's change,
and the optional reason. A secret is never written here (its routes record nothing). Migration
``0033`` adds a trigger that refuses UPDATE and DELETE.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Identity, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ..core.database import Base

#: The longest reason accepted (the route and the table agree).
REASON_MAX_CHARS = 500


class SettingChange(Base):
    __tablename__ = "dw_setting_changes"
    __table_args__ = (
        CheckConstraint(
            "origin IN ('operator', 'agent') AND length(actor) > 0", name="actor_valid"
        ),
        CheckConstraint(
            f"reason IS NULL OR length(reason) <= {REASON_MAX_CHARS}", name="reason_length"
        ),
        Index("ix_dw_setting_changes_key", "key", "changed_at"),
        Index("ix_dw_setting_changes_changed_at", "changed_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    previous_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    approval_id: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str | None] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
