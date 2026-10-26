"""Recipe drafts REST (FR-002.18, FR-002.48; FTDD 002 section 5.2)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.agent_origin import Actor, get_actor, resolve_who
from ....core.database import get_db
from ....schemas.drafts import DraftIn, DraftList, DraftOut, DraftSaveIn
from ....schemas.recipes import RevisionOut
from ....services import draft_service, recipe_service

router = APIRouter(prefix="/api/v1/recipe-drafts", tags=["recipes"])


@router.get("", response_model=DraftList)
async def list_drafts(
    dataset_id: str | None = Query(None), db: AsyncSession = Depends(get_db)
) -> DraftList:
    rows = await draft_service.list_drafts(db, dataset_id)
    return DraftList(items=[draft_service.out(r) for r in rows], total=len(rows))


@router.post("", response_model=DraftOut, status_code=201)
async def create_draft(
    body: DraftIn, db: AsyncSession = Depends(get_db), actor: Actor = Depends(get_actor)
) -> DraftOut:
    who = await resolve_who(actor, db)
    return draft_service.out(await draft_service.create(db, who, body.model_dump()))


@router.get("/{draft_id}", response_model=DraftOut)
async def get_draft(draft_id: str, db: AsyncSession = Depends(get_db)) -> DraftOut:
    return draft_service.out(await draft_service.get(db, draft_id))


@router.put("/{draft_id}", response_model=DraftOut)
async def update_draft(
    draft_id: str,
    body: DraftIn,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> DraftOut:
    """Save as the user goes; the body may be invalid."""
    who = await resolve_who(actor, db)
    return draft_service.out(await draft_service.update(db, who, draft_id, body.model_dump()))


@router.delete("/{draft_id}", status_code=204, response_class=Response)
async def delete_draft(draft_id: str, db: AsyncSession = Depends(get_db)) -> Response:
    await draft_service.delete(db, draft_id)
    return Response(status_code=204)


@router.post("/{draft_id}/save", response_model=RevisionOut, status_code=201)
async def save_draft(
    draft_id: str,
    body: DraftSaveIn,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> RevisionOut:
    """Validate and save the draft as a recipe revision; refuses an invalid draft per step."""
    who = await resolve_who(actor, db)
    revision = await draft_service.save_as_revision(db, who, draft_id, body.recipe_name)
    return await recipe_service.revision_out(db, revision)
