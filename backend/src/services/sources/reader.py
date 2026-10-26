"""The reader API features 002 and 008 use to read sources (001 FTASKS 9.6; 001 FTDD section 6.1).

Every reader refuses a source that is not ``ready``: a source is pinned and frozen only once it is
ready (FR-001.5, FR-001.31), and feature 002 builds only from pinned inputs (FR-002.7). The locator
format is ``"<split>:<row_index>"`` over ``sources/<id>/<file>`` (FR-001.6).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ...core.errors import ConflictError, NotFoundError
from ...models.source import Source, SourceFile
from ...models.source_enums import SourceKind, SourceState


@dataclass(frozen=True)
class ReadySource:
    id: str
    kind: str
    display_name: str
    repo_id: str | None
    config: str | None
    resolved_commit: str | None
    content_hash: str | None
    licence_raw: Any
    licence_display: str
    licence_origin: str | None
    detection: dict[str, Any] | None

    @property
    def pin(self) -> dict[str, Any]:
        """What pins this source: a commit for HF, a content hash for an upload."""
        if self.kind == SourceKind.HF:
            return {"revision": self.resolved_commit}
        return {"content_hash": self.content_hash}


@dataclass(frozen=True)
class SourceFileInfo:
    split: str
    path: str
    rows: int
    bytes: int
    sha256: str
    columns: list[dict[str, Any]]


def locator(split: str, row_index: int) -> str:
    return f"{split}:{row_index}"


def _uuid(value: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        raise NotFoundError(
            f"{value!r} is not a valid source id.", code="source_not_found"
        ) from None


def _ready(row: Source | None, source_id: str) -> ReadySource:
    if row is None:
        raise NotFoundError(f"No source {source_id}.", code="source_not_found")
    if row.state != SourceState.READY:
        raise ConflictError(
            f"Source {row.display_name} is {row.state}; only a ready source can be read. "
            "Wait for its import to finish, or import it again.",
            code="source_not_ready",
            details={"source_id": row.id, "state": row.state},
        )
    pinned = row.resolved_commit if row.kind == SourceKind.HF else row.content_hash
    if not pinned:
        raise ConflictError(
            f"Source {row.display_name} has no pinned revision or content hash.",
            code="source_unpinned",
            details={"source_id": row.id},
        )
    return ReadySource(
        id=row.id,
        kind=row.kind,
        display_name=row.display_name,
        repo_id=row.repo_id,
        config=row.config,
        resolved_commit=row.resolved_commit,
        content_hash=row.content_hash,
        licence_raw=row.licence_raw,
        licence_display=row.licence_display,
        licence_origin=row.licence_origin,
        detection=row.detection,
    )


def _file(row: SourceFile) -> SourceFileInfo:
    return SourceFileInfo(row.split, row.path, row.rows, row.bytes, row.sha256, list(row.columns))


async def get_ready_source(db: AsyncSession, source_id: str) -> ReadySource:
    return _ready(await db.get(Source, _uuid(source_id), populate_existing=True), source_id)


async def list_files(db: AsyncSession, source_id: str) -> list[SourceFileInfo]:
    await get_ready_source(db, source_id)
    rows = await db.execute(
        select(SourceFile)
        .where(SourceFile.source_id == _uuid(source_id))
        .order_by(SourceFile.split)
    )
    return [_file(r) for r in rows.scalars()]


def get_ready_source_sync(db: Session, source_id: str) -> ReadySource:
    return _ready(db.get(Source, _uuid(source_id), populate_existing=True), source_id)


def list_files_sync(db: Session, source_id: str) -> list[SourceFileInfo]:
    get_ready_source_sync(db, source_id)
    rows = db.execute(
        select(SourceFile)
        .where(SourceFile.source_id == _uuid(source_id))
        .order_by(SourceFile.split)
    )
    return [_file(r) for r in rows.scalars()]


def detection(source: ReadySource) -> dict[str, Any] | None:
    return source.detection
