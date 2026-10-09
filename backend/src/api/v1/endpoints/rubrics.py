"""Rubric library routes (FR-005.16, FR-005.17; FTDD 005 section 5.1)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.agent_origin import Actor, get_actor, resolve_who
from ....core.database import get_db
from ....schemas.labeling import (
    RubricClone,
    RubricCreate,
    RubricExport,
    RubricOut,
)
from ....services import decision_template_service as svc

router = APIRouter(prefix="/api/v1/rubrics", tags=["labeling"])


async def _who(actor: Actor, db: AsyncSession) -> svc.Who:
    who = await resolve_who(actor, db)
    return svc.Who(who.who, who.origin)


@router.get("", response_model=list[RubricOut])
async def list_rubrics(db: AsyncSession = Depends(get_db)) -> list[RubricOut]:
    return [await svc.rubric_out(db, row) for row in await svc.list_rubrics(db)]


@router.post("", response_model=RubricOut, status_code=201)
async def create_rubric(
    body: RubricCreate,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> RubricOut:
    """A new rubric, or a new version of a name (an edit is always a new version)."""
    row = await svc.create_rubric(db, body.name, body.body, await _who(actor, db))
    return await svc.rubric_out(db, row)


@router.post("/import", response_model=RubricOut, status_code=201)
async def import_rubric(
    body: RubricExport,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> RubricOut:
    """Validated before saving; a document already stored returns the stored row."""
    row = await svc.import_rubric(db, body, await _who(actor, db))
    return await svc.rubric_out(db, row)


@router.get("/{rubric_id}", response_model=RubricOut)
async def get_rubric(rubric_id: str, db: AsyncSession = Depends(get_db)) -> RubricOut:
    return await svc.rubric_out(db, await svc.get_rubric(db, rubric_id))


@router.post("/{rubric_id}/clone", response_model=RubricOut, status_code=201)
async def clone_rubric(
    rubric_id: str,
    body: RubricClone,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> RubricOut:
    row = await svc.clone_rubric(db, rubric_id, body.body, await _who(actor, db))
    return await svc.rubric_out(db, row)


@router.get("/{rubric_id}/export", response_model=RubricExport)
async def export_rubric(rubric_id: str, db: AsyncSession = Depends(get_db)) -> RubricExport:
    return svc.export_rubric(await svc.get_rubric(db, rubric_id))
