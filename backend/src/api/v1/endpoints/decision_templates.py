"""Decision-template library routes (FR-005.11 – FR-005.17; FTDD 005 section 5.1)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.agent_origin import Actor, get_actor, resolve_who
from ....core.database import get_db
from ....schemas.labeling import (
    DecisionTemplateClone,
    DecisionTemplateCreate,
    DecisionTemplateExport,
    DecisionTemplateOut,
)
from ....services import decision_template_service as svc

router = APIRouter(prefix="/api/v1/decision-templates", tags=["labeling"])


async def _who(actor: Actor, db: AsyncSession) -> svc.Who:
    who = await resolve_who(actor, db)
    return svc.Who(who.who, who.origin)


@router.get("", response_model=list[DecisionTemplateOut])
async def list_templates(db: AsyncSession = Depends(get_db)) -> list[DecisionTemplateOut]:
    return [await svc.template_out(db, row) for row in await svc.list_templates(db)]


@router.post("", response_model=DecisionTemplateOut, status_code=201)
async def create_template(
    body: DecisionTemplateCreate,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> DecisionTemplateOut:
    """A new template, or a new version of a name (an edit is always a new version)."""
    row = await svc.create_template(db, body.name, body.body, await _who(actor, db))
    return await svc.template_out(db, row)


@router.post("/import", response_model=DecisionTemplateOut, status_code=201)
async def import_template(
    body: DecisionTemplateExport,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> DecisionTemplateOut:
    """Validated before saving; a document already stored returns the stored row."""
    row = await svc.import_template(db, body, await _who(actor, db))
    return await svc.template_out(db, row)


@router.get("/{template_id}", response_model=DecisionTemplateOut)
async def get_template(template_id: str, db: AsyncSession = Depends(get_db)) -> DecisionTemplateOut:
    return await svc.template_out(db, await svc.get_template(db, template_id))


@router.post("/{template_id}/clone", response_model=DecisionTemplateOut, status_code=201)
async def clone_template(
    template_id: str,
    body: DecisionTemplateClone,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> DecisionTemplateOut:
    row = await svc.clone_template(db, template_id, body.body, await _who(actor, db))
    return await svc.template_out(db, row)


@router.get("/{template_id}/export", response_model=DecisionTemplateExport)
async def export_template(
    template_id: str, db: AsyncSession = Depends(get_db)
) -> DecisionTemplateExport:
    return svc.export_template(await svc.get_template(db, template_id))
