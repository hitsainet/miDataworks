"""Audit routes (FR-006.27 – FR-006.29): draw a version's stratified audit sample and read its
status. 008's check C-5 reads the same status in-process (``audit_service.status``)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.agent_origin import Actor, get_actor, resolve_who
from ....core.database import get_db
from ....schemas.review import AuditDraw, AuditStatusOut
from ....services.review import audit_service

router = APIRouter(prefix="/api/v1/versions", tags=["review"])


@router.post("/{version_id}/audit", response_model=AuditStatusOut, status_code=201)
async def draw_audit(
    version_id: str,
    body: AuditDraw,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> AuditStatusOut:
    """Draw 50 to 100 rows (default 100), stratified; supersedes an in-progress audit."""
    who = await resolve_who(actor, db)
    audit = await audit_service.draw(db, version_id, body, who)
    return await db.run_sync(lambda s: audit_service.status(str(audit.version_id), session=s))


@router.get("/{version_id}/audit", response_model=AuditStatusOut)
async def get_audit_status(version_id: str, db: AsyncSession = Depends(get_db)) -> AuditStatusOut:
    """``none``, ``in_progress`` or ``complete``, with the result once complete."""
    return await db.run_sync(lambda s: audit_service.status(version_id, session=s))
