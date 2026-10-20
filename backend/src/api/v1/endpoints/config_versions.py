"""Selector and grader configuration versions for miForge (FR-008.42; M3)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.agent_origin import Actor, get_actor, resolve_who
from ....core.database import get_db, get_sync_db
from ....models.publish import ConfigVersion
from ....schemas.publishing import ConfigVersionIn, ConfigVersionList, ConfigVersionOut
from ....services.exports import config_versions as service

router = APIRouter(prefix="/api/v1/config-versions", tags=["config-versions"])


def _out(c: ConfigVersion) -> ConfigVersionOut:
    return ConfigVersionOut(
        id=c.id,
        kind=c.kind,
        name=c.name,
        number=c.number,
        plugin=c.plugin,
        body=c.body,
        body_sha256=c.body_sha256,
        body_format=service.BODY_FORMATS[c.kind],
        parent_id=c.parent_id,
        created_by=c.created_by,
        created_by_origin=c.created_by_origin,
        created_at=c.created_at,
    )


@router.post("", response_model=ConfigVersionOut, status_code=201)
async def create_config_version(
    body: ConfigVersionIn, db: AsyncSession = Depends(get_db), actor: Actor = Depends(get_actor)
) -> ConfigVersionOut:
    who = await resolve_who(actor, db)

    def run() -> ConfigVersionOut:
        with get_sync_db() as session:
            row = service.create(
                session,
                kind=body.kind,
                name=body.name,
                body=body.body,
                parent_id=body.parent_id,
                created_by=who.who,
                origin=who.origin,
            )
            return _out(row)

    return await run_in_threadpool(run)


@router.get("", response_model=ConfigVersionList)
async def list_config_versions(kind: str | None = Query(None, max_length=16)) -> ConfigVersionList:
    def run() -> ConfigVersionList:
        with get_sync_db() as session:
            rows = service.list_versions(session, kind)
            return ConfigVersionList(items=[_out(r) for r in rows], total=len(rows))

    return await run_in_threadpool(run)


@router.get("/{config_id}", response_model=ConfigVersionOut)
async def get_config_version(config_id: str) -> ConfigVersionOut:
    def run() -> ConfigVersionOut:
        with get_sync_db() as session:
            return _out(service.get(session, config_id))

    return await run_in_threadpool(run)
