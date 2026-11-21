"""Datasets REST (FR-002.1, 002.4, 002.44, 002.45; FTDD 002 section 5.2)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.agent_origin import Actor, get_actor, resolve_who
from ....core.database import get_db
from ....models.enums import TargetType
from ....schemas.archive import ArchiveRequest
from ....schemas.datasets import (
    DatasetCreate,
    DatasetList,
    DatasetOut,
    DatasetPatch,
    DatasetsMeta,
)
from ....services import archive_service, dataset_service
from ._paging import Page, paging

router = APIRouter(prefix="/api/v1/datasets", tags=["datasets"])


@router.get("/meta", response_model=DatasetsMeta)
async def datasets_meta() -> DatasetsMeta:
    """Target types, defaults, event kinds, states, schemes and guided steps, from their sources."""
    return dataset_service.meta()


@router.get("", response_model=DatasetList)
async def list_datasets(
    q: str | None = Query(None, max_length=100),
    target_type: TargetType | None = Query(None),
    include_archived: bool = Query(False, description="Also list archived datasets."),
    page: Page = Depends(paging),
    db: AsyncSession = Depends(get_db),
) -> DatasetList:
    items, total = await dataset_service.list_datasets(
        db,
        q=q,
        target_type=target_type.value if target_type else None,
        page=page.page,
        limit=page.limit,
        include_archived=include_archived,
    )
    return DatasetList(items=items, total=total, page=page.page, limit=page.limit)


@router.post("", response_model=DatasetOut, status_code=201)
async def create_dataset(
    body: DatasetCreate,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> DatasetOut:
    who = await resolve_who(actor, db)
    row = await dataset_service.create(
        db, who, name=body.name, target_type=body.target_type.value, description=body.description
    )
    return await dataset_service.dataset_out(db, row)


@router.get("/{dataset_id}", response_model=DatasetOut)
async def get_dataset(dataset_id: str, db: AsyncSession = Depends(get_db)) -> DatasetOut:
    return await dataset_service.dataset_out(
        db, await dataset_service.get_dataset_row(db, dataset_id)
    )


@router.patch("/{dataset_id}", response_model=DatasetOut)
async def patch_dataset(
    dataset_id: str, body: DatasetPatch, db: AsyncSession = Depends(get_db)
) -> DatasetOut:
    fields = body.model_dump(exclude_unset=True, mode="json")
    row = await dataset_service.patch(db, dataset_id, fields=fields)
    return await dataset_service.dataset_out(db, row)


@router.post("/{dataset_id}/archive", response_model=DatasetOut)
async def archive_dataset(
    dataset_id: str,
    body: ArchiveRequest | None = None,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> DatasetOut:
    """Hide the dataset from default lists and pickers. Nothing is deleted; refused
    ``dataset_busy`` while a job works on it."""
    who = await resolve_who(actor, db)
    row = await archive_service.set_archived(
        db, "dataset", dataset_id, who, archived=True, reason=body.reason if body else None
    )
    return await dataset_service.dataset_out(db, row)


@router.post("/{dataset_id}/unarchive", response_model=DatasetOut)
async def unarchive_dataset(
    dataset_id: str,
    body: ArchiveRequest | None = None,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> DatasetOut:
    who = await resolve_who(actor, db)
    row = await archive_service.set_archived(
        db, "dataset", dataset_id, who, archived=False, reason=body.reason if body else None
    )
    return await dataset_service.dataset_out(db, row)
