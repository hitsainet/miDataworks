"""Version reads: detail and lists with head and supersession computed (FR-002.4; FTDD 002 §4.7).

Head and supersession are never stored: the head is the highest-numbered completed version of a
dataset, computed here with a window function, so a deleted head is never shown as current and a
new version supersedes the old one without touching it (FR-002.3).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.errors import NotFoundError
from ..models.dataset import Dataset
from ..models.enums import VersionState
from ..models.recipe import RecipeRevision
from ..models.version import Version
from ..schemas.versions import SplitOut, VersionOut, VersionSummary


@dataclass(frozen=True)
class Supersession:
    is_head: bool
    superseded_by: int | None


async def head_numbers(db: AsyncSession, dataset_ids: list[str]) -> dict[str, int]:
    """dataset id -> its head version number (completed versions only)."""
    if not dataset_ids:
        return {}
    rows = await db.execute(
        select(Version.dataset_id, func.max(Version.number))
        .where(Version.dataset_id.in_(dataset_ids), Version.state == VersionState.COMPLETED)
        .group_by(Version.dataset_id)
    )
    return {str(d): int(n) for d, n in rows.all()}


def supersession(version: Version, head: int | None) -> Supersession:
    if version.state != VersionState.COMPLETED or head is None:
        return Supersession(False, head)
    if version.number == head:
        return Supersession(True, None)
    return Supersession(False, head)


def _uuid(value: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        raise NotFoundError(
            f"{value!r} is not a valid version id.", code="version_not_found"
        ) from None


async def get_version_row(db: AsyncSession, version_id: str) -> Version:
    row = await db.get(Version, _uuid(version_id), populate_existing=True)
    if row is None:
        raise NotFoundError(f"No version {version_id}.", code="version_not_found")
    return row


async def version_out(db: AsyncSession, version_id: str) -> VersionOut:
    v = await get_version_row(db, version_id)
    dataset = await db.get(Dataset, v.dataset_id)
    assert dataset is not None
    heads = await head_numbers(db, [v.dataset_id])
    s = supersession(v, heads.get(v.dataset_id))
    revision = await db.get(RecipeRevision, v.recipe_revision_id)
    return VersionOut(
        id=v.id,
        dataset_id=v.dataset_id,
        dataset_name=dataset.name,
        target_type=dataset.target_type,
        number=v.number,
        state=v.state,
        is_head=s.is_head,
        superseded_by=s.superseded_by,
        parent_version_id=v.parent_version_id,
        request_digest=v.request_digest,
        inputs=list(v.inputs),
        recipe_hash=v.recipe_hash,
        recipe_revision_id=v.recipe_revision_id,
        recipe_id=revision.recipe_id if revision else None,
        seed=int(v.seed),
        bindings=list(v.bindings),
        rowkey_scheme=v.rowkey_scheme,
        column_roles=dict(v.column_roles),
        splits=[SplitOut(**split) for split in v.splits],
        total_rows=int(v.total_rows),
        total_bytes=int(v.total_bytes),
        held_out_origin_version_id=v.held_out_origin_version_id,
        warnings=list(v.warnings),
        drop_summary=list(v.drop_summary),
        manifest_sha256=v.manifest_sha256,
        build_job_id=v.build_job_id,
        created_by=v.created_by,
        created_by_origin=v.created_by_origin,
        created_at=v.created_at,
        deleted_by=v.deleted_by,
        deleted_by_origin=v.deleted_by_origin,
        deleted_at=v.deleted_at,
        delete_reason=v.delete_reason,
    )


def summary(v: Version, dataset: Dataset, head: int | None) -> VersionSummary:
    s = supersession(v, head)
    return VersionSummary(
        id=v.id,
        dataset_id=v.dataset_id,
        dataset_name=dataset.name,
        target_type=dataset.target_type,
        number=v.number,
        state=v.state,
        is_head=s.is_head,
        superseded_by=s.superseded_by,
        parent_version_id=v.parent_version_id,
        total_rows=int(v.total_rows),
        total_bytes=int(v.total_bytes),
        warnings_count=len(v.warnings),
        recipe_hash=v.recipe_hash,
        seed=int(v.seed),
        created_by=v.created_by,
        created_at=v.created_at,
    )


async def list_versions(
    db: AsyncSession,
    *,
    dataset_id: str | None = None,
    state: str | None = None,
    page: int = 1,
    limit: int = 50,
) -> tuple[list[VersionSummary], int]:
    query: Any = select(Version, Dataset).join(Dataset, Dataset.id == Version.dataset_id)
    count: Any = select(func.count()).select_from(Version)
    if dataset_id:
        try:
            did = str(uuid.UUID(dataset_id))
        except ValueError:
            return [], 0
        query = query.where(Version.dataset_id == did)
        count = count.where(Version.dataset_id == did)
    if state:
        query = query.where(Version.state == state)
        count = count.where(Version.state == state)
    total = int((await db.execute(count)).scalar_one())
    rows = (
        await db.execute(
            query.order_by(Dataset.name, Version.number.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
    ).all()
    heads = await head_numbers(db, sorted({str(v.dataset_id) for v, _ in rows}))
    return [summary(v, d, heads.get(str(v.dataset_id))) for v, d in rows], total
