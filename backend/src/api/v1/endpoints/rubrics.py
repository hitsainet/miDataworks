"""Rubric library routes (FR-005.16, FR-005.17; FTDD 005 section 5.1)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.agent_origin import Actor, get_actor, resolve_who
from ....core.database import get_db
from ....schemas.behaviour import BEHAVIOUR_KEY_PATTERN
from ....schemas.labeling import (
    RubricBehaviourPatch,
    RubricClone,
    RubricCreate,
    RubricExport,
    RubricOut,
)
from ....services import decision_template_service as svc
from ._paging import OptionalPage, Paged, optional_paging

router = APIRouter(prefix="/api/v1/rubrics", tags=["labeling"])


async def _who(actor: Actor, db: AsyncSession) -> svc.Who:
    who = await resolve_who(actor, db)
    return svc.Who(who.who, who.origin)


@router.get("", response_model=list[RubricOut] | Paged[RubricOut])
async def list_rubrics(
    behaviour_key: str | None = Query(
        None, pattern=BEHAVIOUR_KEY_PATTERN, description="Only rubrics for this behaviour (015)."
    ),
    paging: OptionalPage = Depends(optional_paging),
    db: AsyncSession = Depends(get_db),
) -> list[RubricOut] | Paged[RubricOut]:
    """Every rubric, or one page of them with the total when ``page``/``limit`` are given."""
    rows, meta = paging.cut(list(await svc.list_rubrics(db, behaviour_key=behaviour_key)))
    items = [await svc.rubric_out(db, row) for row in rows]
    return Paged[RubricOut](items=items, **meta) if paging.asked else items


@router.post("", response_model=RubricOut, status_code=201)
async def create_rubric(
    body: RubricCreate,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> RubricOut:
    """A new rubric, or a new version of a name (an edit is always a new version)."""
    row = await svc.create_rubric(
        db, body.name, body.body, await _who(actor, db), behaviour_key=body.behaviour_key
    )
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
    key = body.behaviour_key if "behaviour_key" in body.model_fields_set else svc.INHERIT
    row = await svc.clone_rubric(db, rubric_id, body.body, await _who(actor, db), key)
    return await svc.rubric_out(db, row)


@router.patch("/{rubric_id}", response_model=RubricOut)
async def set_rubric_behaviour(
    rubric_id: str, body: RubricBehaviourPatch, db: AsyncSession = Depends(get_db)
) -> RubricOut:
    """Change ONLY the behaviour key of an unused rubric (015 C4, FR-015.8); ``409
    TEMPLATE_IMMUTABLE`` once a label run used it (D58). The body never changes in place."""
    row = await svc.set_rubric_behaviour(db, rubric_id, body.behaviour_key)
    return await svc.rubric_out(db, row)


@router.get("/{rubric_id}/export", response_model=RubricExport)
async def export_rubric(rubric_id: str, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """``midataworks.rubric/v1`` (``docs/schemas/midataworks-rubric-v1.json``); ``behaviour`` only
    when recorded."""
    return JSONResponse(svc.export_rubric_document(await svc.get_rubric(db, rubric_id)))
