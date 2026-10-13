"""``dw_datasets``: a named series of versions (FR-002.1; FTDD 002 section 4.2).

``target_type`` may change only while the dataset has no version; the service refuses and a
trigger (migration ``0008``) refuses too, so a future code path cannot bypass it.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from ..core.database import Base
from .enums import ActorOrigin, TargetType, check_in

DATASET_NAME_PATTERN = "^[a-z0-9][a-z0-9-]{0,99}$"


class Dataset(Base):
    __tablename__ = "dw_datasets"
    __table_args__ = (
        CheckConstraint(f"name ~ '{DATASET_NAME_PATTERN}'", name="name_pattern"),
        CheckConstraint(check_in("target_type", TargetType), name="target_type_valid"),
        CheckConstraint(check_in("created_by_origin", ActorOrigin), name="origin_valid"),
        CheckConstraint("length(created_by) > 0", name="created_by_present"),
        CheckConstraint("next_version_number >= 1", name="next_number_positive"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    next_version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
