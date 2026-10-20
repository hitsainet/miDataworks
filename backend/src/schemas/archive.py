"""Archive request and the fields every archivable item reports (operator decision 2026-10-20)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from ..models.archive import Archivable


class ArchiveFields(BaseModel):
    """Required, not defaulted: an output built without them fails instead of saying "shown"."""

    archived_at: datetime | None
    archived_by: str | None
    archive_reason: str | None


class ArchiveRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


def archive_fields(row: Archivable) -> dict[str, object]:
    return {
        "archived_at": row.archived_at,
        "archived_by": row.archived_by,
        "archive_reason": row.archive_reason,
    }
