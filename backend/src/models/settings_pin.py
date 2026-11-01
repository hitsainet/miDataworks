"""``dw_settings_pin``: the Settings PIN's hash, one row at most (feature 014; operator directive
2026-10-20).

A table of its own, not a ``dw_app_settings`` row: miStudio kept its PIN hash as an ordinary setting,
and the generic settings routes could then read, overwrite or delete it, which removed the PIN
without knowing it. No settings route reaches this table; only ``services/settings_pin_service``.

The value is a PBKDF2-SHA256 hash with a random salt (``core/settings_pin.py``), never the PIN.
Migration ``0034``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, SmallInteger, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ..core.database import Base


class SettingsPin(Base):
    __tablename__ = "dw_settings_pin"
    __table_args__ = (CheckConstraint("id = 1", name="single_row"),)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1)
    pin_hash: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
