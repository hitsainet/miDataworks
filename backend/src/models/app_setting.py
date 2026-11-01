# Origin: miStudio (Onegaishimas/miStudio) backend/src/models/app_setting.py @ c829a2cc
# Mode: adapt (docs/REUSE.md): table renamed `dw_app_settings` (ADR-003 prefix), typed columns,
# string primary key instead of a UUID.
"""``dw_app_settings``: key-value settings, secrets encrypted with AES-256-GCM (ADR-015)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ..core.database import Base


class AppSetting(Base):
    __tablename__ = "dw_app_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    #: Plaintext for ordinary settings; base64 AES-GCM envelope when ``is_sensitive``.
    value: Mapped[str] = mapped_column(Text, nullable=False)
    is_sensitive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False, default="general", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
