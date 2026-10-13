"""Exports REST: TRL files and miForge sets (FR-008.25–008.29, 008.41, 008.52; FTDD 008 §5.2).

No route here writes to the Hub, so none is approval-gated (FR-008.52); a miForge set reaches
miForge through ``POST /publishes`` (gated). Downloads are confined to ``exports/<id>/``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.agent_origin import Actor, get_actor, resolve_who
from ....core.database import get_db, get_sync_db
from ....core.errors import NotFoundError
from ....models.publish import Export
from ....schemas.publishing import ExportAccepted, ExportIn, ExportList, ExportOut
from ....services.exports import export_service
from ....services.job_service import dispatch_queued
from ._paging import Page, paging

router = APIRouter(prefix="/api/v1/exports", tags=["exports"])


def _out(e: Export) -> ExportOut:
    return ExportOut(
        id=e.id,
        job_id=e.job_id,
        target=e.target,
        version_id=e.version_id,
        config_version_id=e.config_version_id,
        params=e.params,
        trl_version=e.trl_version,
        status=e.status,
        files=e.files,
        manifest_sha256=e.manifest_sha256,
        error=e.error,
        started_by=e.started_by,
        started_by_origin=e.started_by_origin,
        created_at=e.created_at,
        completed_at=e.completed_at,
    )


def _dispatch() -> list[str]:
    with get_sync_db() as session:
        return dispatch_queued(session)


@router.post("", response_model=ExportAccepted, status_code=202)
async def create_export(
    body: ExportIn, db: AsyncSession = Depends(get_db), actor: Actor = Depends(get_actor)
) -> ExportAccepted:
    """Start an export. Formability and 004's validation are checked before any job starts."""
    who = await resolve_who(actor, db)

    def run() -> ExportAccepted:
        with get_sync_db() as session:
            row, job = export_service.request_export(
                session, body.model_dump(), started_by=who.who, origin=who.origin
            )
            return ExportAccepted(export_id=row.id, job_id=job.id)

    accepted = await run_in_threadpool(run)
    await run_in_threadpool(_dispatch)
    return accepted


@router.get("", response_model=ExportList)
async def list_exports(
    version_id: str | None = Query(None, max_length=64),
    page: Page = Depends(paging),
    db: AsyncSession = Depends(get_db),
) -> ExportList:
    query = select(Export)
    count = select(func.count()).select_from(Export)
    if version_id:
        query = query.where(Export.version_id == version_id)
        count = count.where(Export.version_id == version_id)
    total = int((await db.execute(count)).scalar_one())
    rows = (
        await db.execute(
            query.order_by(Export.created_at.desc(), Export.id)
            .offset((page.page - 1) * page.limit)
            .limit(page.limit)
        )
    ).scalars()
    return ExportList(items=[_out(e) for e in rows], total=total, page=page.page, limit=page.limit)


@router.get("/{export_id}", response_model=ExportOut)
async def get_export(export_id: str, db: AsyncSession = Depends(get_db)) -> ExportOut:
    row = await db.get(Export, export_id, populate_existing=True)
    if row is None:
        raise NotFoundError(f"No export {export_id}.", code="export_not_found")
    return _out(row)


@router.get("/{export_id}/files/{name}")
async def download_export_file(export_id: str, name: str) -> FileResponse:
    def run() -> str:
        with get_sync_db() as session:
            return str(export_service.export_file(session, export_id, name))

    path = await run_in_threadpool(run)
    return FileResponse(path, filename=name)
