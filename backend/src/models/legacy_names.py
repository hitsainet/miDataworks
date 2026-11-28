"""Old sibling environment names in use (feature 020, part B; FTID §4.1, FTDD §4.2).

``dw_legacy_name_uses``: one row per old name (``kind``, ``old_name``) a miDataworks process read,
with the first and last time any process (API, worker, beat) was seen reading it and how many
touches. NAMES ONLY, never a value. It is the evidence P7 retires an old name on (7 quiet days):
a row stays after the name goes quiet, as the record.

``dw_legacy_name_write_errors``: one row per reporter (``<role>@<host>``) whose last upsert into
the first table failed and has not succeeded since (``cleared_at`` NULL), so a worker that cannot
record its evidence makes health's ``legacy_names_meta.error`` say so (CF-25 m2) instead of reading
as "nothing in use". Migration ``0048``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ..core.database import Base

#: miDataworks aliases no hostname and serves no renamed header or tool: environment names only.
LEGACY_NAME_KINDS: tuple[str, ...] = ("env",)


class LegacyNameUse(Base):
    __tablename__ = "dw_legacy_name_uses"
    __table_args__ = (CheckConstraint("kind IN ('env')", name="kind_valid"),)

    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    old_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    new_name: Mapped[str] = mapped_column(String(64), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="1")


class LegacyNameWriteError(Base):
    __tablename__ = "dw_legacy_name_write_errors"

    reporter: Mapped[str] = mapped_column(String(128), primary_key=True)
    error: Mapped[str] = mapped_column(String(128), nullable=False)
    failed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
