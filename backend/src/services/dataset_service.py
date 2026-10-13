"""Datasets: list, create, get, patch, and the metadata the UI reads (FR-002.1, 002.4, 002.44).

The target type may change only while the dataset has no version (task 3.7). The service refuses
with ``dataset_has_versions``; the ``dw_datasets_target_type`` trigger refuses too, so a code path
that skips this check still cannot change it.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.agent_origin import Who
from ..core.errors import ConflictError, NotFoundError
from ..models.dataset import Dataset
from ..models.enums import (
    GUIDED_STEPS,
    BindingKind,
    ColumnRole,
    EventKind,
    InputKind,
    TargetType,
    VersionState,
    values,
)
from ..models.version import Version
from ..schemas.datasets import DatasetOut, DatasetsMeta, DatasetSummary
from .assembly import DEFAULT_CONTENT_COLUMNS
from .row_keys import ROWKEY_SCHEMES, ROWKEY_V1
from .version_read_service import head_numbers, list_versions


def meta() -> DatasetsMeta:
    """Built from the enums, the row-key scheme registry and the defaults table, never a copy."""
    return DatasetsMeta(
        target_types=values(TargetType),
        default_content_columns={k: list(v) for k, v in DEFAULT_CONTENT_COLUMNS.items()},
        event_kinds=values(EventKind),
        version_states=values(VersionState),
        input_kinds=values(InputKind),
        column_roles=values(ColumnRole),
        rowkey_schemes=sorted(ROWKEY_SCHEMES),
        default_rowkey_scheme=ROWKEY_V1,
        binding_kinds=values(BindingKind),
        guided_steps=list(GUIDED_STEPS),
    )


def _uuid(value: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        raise NotFoundError(
            f"{value!r} is not a valid dataset id.", code="dataset_not_found"
        ) from None


async def get_dataset_row(db: AsyncSession, dataset_id: str) -> Dataset:
    row = await db.get(Dataset, _uuid(dataset_id), populate_existing=True)
    if row is None:
        raise NotFoundError(f"No dataset {dataset_id}.", code="dataset_not_found")
    return row


async def create(
    db: AsyncSession, who: Who, *, name: str, target_type: str, description: str | None
) -> Dataset:
    taken = (await db.execute(select(Dataset.id).where(Dataset.name == name))).scalar_one_or_none()
    if taken is not None:
        raise ConflictError(
            f"A dataset named {name!r} already exists. Choose another name.",
            code="dataset_name_taken",
            details={"dataset_id": taken},
        )
    row = Dataset(
        id=str(uuid.uuid4()),
        name=name,
        target_type=target_type,
        description=description,
        next_version_number=1,
        created_by=who.who,
        created_by_origin=who.origin,
    )
    db.add(row)
    await db.commit()
    return row


async def patch(db: AsyncSession, dataset_id: str, *, fields: dict[str, Any]) -> Dataset:
    row = await get_dataset_row(db, dataset_id)
    new_type = fields.get("target_type")
    if new_type is not None and new_type != row.target_type:
        has_versions = (
            await db.execute(select(Version.id).where(Version.dataset_id == row.id).limit(1))
        ).first()
        if has_versions is not None:
            raise ConflictError(
                f"{row.name} already has versions, so its target type is fixed at "
                f"{row.target_type}. Create a new dataset for a different goal.",
                code="dataset_has_versions",
                details={"target_type": row.target_type},
            )
        row.target_type = new_type
    if "description" in fields:
        row.description = fields["description"]
    await db.commit()
    return row


async def _summaries(db: AsyncSession, rows: list[Dataset]) -> list[DatasetSummary]:
    ids = [r.id for r in rows]
    heads = await head_numbers(db, ids)
    counts: dict[str, int] = {}
    if ids:
        counts = {
            str(d): int(n)
            for d, n in (
                await db.execute(
                    select(Version.dataset_id, func.count())
                    .where(Version.dataset_id.in_(ids))
                    .group_by(Version.dataset_id)
                )
            ).all()
        }
    head_rows: dict[str, Version] = {}
    if heads:
        found = await db.execute(
            select(Version).where(
                Version.dataset_id.in_(list(heads)),
                Version.state == VersionState.COMPLETED,
                Version.number.in_(list(set(heads.values()))),
            )
        )
        for v in found.scalars():
            if heads.get(str(v.dataset_id)) == v.number:
                head_rows[str(v.dataset_id)] = v
    out: list[DatasetSummary] = []
    for r in rows:
        head = head_rows.get(r.id)
        out.append(
            DatasetSummary(
                id=r.id,
                name=r.name,
                target_type=r.target_type,
                description=r.description,
                head_number=head.number if head else None,
                head_version_id=head.id if head else None,
                parent_version_id=head.parent_version_id if head else None,
                versions=counts.get(r.id, 0),
                rows=int(head.total_rows) if head else None,
                bytes=int(head.total_bytes) if head else None,
                warnings_count=len(head.warnings) if head else 0,
                state="ready" if head else "empty",
                created_by=r.created_by,
                created_at=r.created_at,
            )
        )
    return out


async def list_datasets(
    db: AsyncSession, *, q: str | None, target_type: str | None, page: int, limit: int
) -> tuple[list[DatasetSummary], int]:
    query: Any = select(Dataset)
    if q:
        query = query.where(Dataset.name.ilike(f"%{q}%"))
    if target_type:
        query = query.where(Dataset.target_type == target_type)
    total = int((await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one())
    rows = list(
        (
            await db.execute(query.order_by(Dataset.name).offset((page - 1) * limit).limit(limit))
        ).scalars()
    )
    return await _summaries(db, rows), total


async def dataset_out(db: AsyncSession, row: Dataset) -> DatasetOut:
    summary = (await _summaries(db, [row]))[0]
    versions, _ = await list_versions(db, dataset_id=row.id, limit=200)
    return DatasetOut(**summary.model_dump(), version_list=versions)
