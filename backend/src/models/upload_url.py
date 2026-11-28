"""``dw_upload_urls``: one row per signed upload URL (G-3, operator decision 2026-11-25).

The row is the record that a URL was issued (who, for which file and split, its byte cap and
expiry) and the single-use claim: ``PUT /api/v1/uploads/{token}`` sets ``used_at`` with a
conditional UPDATE, so a second PUT finds it set and is refused ``upload_url_used``. The token
itself (``services/sources/upload_url.py``) carries only this row's id, the cap and the expiry,
signed; nothing secret is stored here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..core.database import Base


class UploadUrl(Base):
    __tablename__ = "dw_upload_urls"
    __table_args__ = (
        CheckConstraint("max_bytes > 0", name="max_bytes_positive"),
        CheckConstraint("created_by_origin IN ('operator', 'agent')", name="origin_valid"),
        CheckConstraint("length(created_by) > 0", name="created_by_present"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    split: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text)
    csv: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    max_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expected_sha256: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    job_id: Mapped[str | None] = mapped_column(Text)
    source_id: Mapped[str | None] = mapped_column(Text)
