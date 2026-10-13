"""Versions REST (FR-002.2–002.9, 002.16, 002.45, 002.50; FTDD 002 section 5.2)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.agent_origin import Actor, get_actor, requires_approval_when_agent, resolve_who
from ....core.database import get_db
from ....models.enums import VersionState
from ....schemas.versions import (
    BuildAccepted,
    DeleteIn,
    RowPage,
    VerifyAccepted,
    VersionBuildRequest,
    VersionList,
    VersionOut,
)
from ....services import (
    compare_service,
    lineage_service,
    version_delete_service,
    version_read_service,
)
from ....services.agent_label_ledger import build_needs_approval
from ._builds import _dispatch, respond
from ._paging import Page, paging

router = APIRouter(prefix="/api/v1/versions", tags=["versions"])


@router.post(
    "",
    response_model=VersionOut | BuildAccepted,
    status_code=202,
    responses={200: {"model": VersionOut}, 202: {"model": BuildAccepted}},
)
@requires_approval_when_agent("agent_label_rows", when=build_needs_approval)
async def build_version(
    body: VersionBuildRequest,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> Any:
    """Build a version: ``200`` with the existing version for the same request (FR-002.5), or
    ``202`` with the job. An agent build whose recipe labels more rows than P-07 allows waits for
    approval (FR-002.50)."""
    return await respond(db, actor, body)


@router.get("", response_model=VersionList)
async def list_versions(
    dataset_id: str | None = Query(None),
    state: VersionState | None = Query(None),
    page: Page = Depends(paging),
    db: AsyncSession = Depends(get_db),
) -> VersionList:
    items, total = await version_read_service.list_versions(
        db,
        dataset_id=dataset_id,
        state=state.value if state else None,
        page=page.page,
        limit=page.limit,
    )
    return VersionList(items=items, total=total, page=page.page, limit=page.limit)


@router.get("/{version_id}", response_model=VersionOut)
async def get_version(version_id: str, db: AsyncSession = Depends(get_db)) -> VersionOut:
    return await version_read_service.version_out(db, version_id)


@router.get(
    "/{version_id}/manifest",
    response_class=Response,
    responses={200: {"content": {"application/json": {}}}},
)
async def get_manifest(version_id: str, db: AsyncSession = Depends(get_db)) -> Response:
    """The stored manifest bytes, unchanged, with ``ETag: <manifest_sha256>`` — also for a
    tombstoned version, whose record outlives its rows (FR-002.9, FR-002.37)."""
    row = await version_read_service.get_version_row(db, version_id)
    return Response(
        content=row.manifest,
        media_type="application/json",
        headers={"ETag": row.manifest_sha256},
    )


@router.get("/{version_id}/rows", response_model=RowPage)
async def get_rows(
    version_id: str,
    split: str | None = Query(None, max_length=64),
    q: str | None = Query(None, max_length=200),
    where: str | None = Query(
        None, max_length=4000, description="JSON list of {column, op, value}"
    ),
    page: Page = Depends(paging),
    db: AsyncSession = Depends(get_db),
) -> RowPage:
    """Rows from Parquet through DuckDB, with structured filters only (FR-002.33)."""
    result = await lineage_service.rows_page(
        db, version_id, split=split, query=q, where=where, page=page.page, limit=page.limit
    )
    return RowPage(**result)


@router.get("/{version_id}/rows/history")
async def get_row_history(
    version_id: str,
    row_key: str | None = Query(None, max_length=64),
    q: str | None = Query(None, max_length=200),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """ "Why did this row leave?" for a row key (or a unique prefix of 12+), or a text search."""
    return await lineage_service.history(db, version_id, row_key, q)


@router.get("/{version_id}/drop-log")
async def get_drop_log(version_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    return await lineage_service.drop_log(db, version_id)


@router.get("/{version_id}/events")
async def get_events(
    version_id: str,
    step_index: int | None = Query(None, ge=0),
    kind: str | None = Query(None, max_length=16),
    reason_code: str | None = Query(None, max_length=64),
    row_key: str | None = Query(None, max_length=64),
    page: Page = Depends(paging),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await lineage_service.events_page(
        db,
        version_id,
        step_index=step_index,
        kind=kind,
        reason_code=reason_code,
        row_key=row_key,
        page=page.page,
        limit=page.limit,
    )


@router.get("/{version_id}/lineage")
async def get_lineage(version_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    return await lineage_service.lineage(db, version_id)


@router.get("/{version_id}/compare")
async def compare_versions(
    version_id: str,
    with_: str | None = Query(None, alias="with"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Compare with the parent, or with ``?with=<version_id>`` (FR-002.38)."""
    return await compare_service.compare(db, version_id, with_)


@router.post("/{version_id}/verify-rebuild", response_model=VerifyAccepted, status_code=202)
async def verify_rebuild(
    version_id: str,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> VerifyAccepted:
    """Rebuild with step reuse off and compare logical digests (FR-002.6)."""
    who = await resolve_who(actor, db)
    job_id = await version_delete_service.request_verify(db, who, version_id)
    await run_in_threadpool(_dispatch)
    return VerifyAccepted(job_id=job_id)


@router.delete("/{version_id}", response_model=VersionOut)
@requires_approval_when_agent("version_delete")
async def delete_version(
    version_id: str,
    body: DeleteIn,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> VersionOut:
    """Tombstone a version; an agent's request waits for the operator's approval (FR-002.37)."""
    who = await resolve_who(actor, db)
    version = await version_delete_service.delete(db, who, version_id, body.reason)
    return await version_read_service.version_out(db, version.id)
