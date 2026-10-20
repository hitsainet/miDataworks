"""Recipes REST (FR-002.13–002.17, FR-002.45; FTDD 002 section 5.2).

Shape follows miStudio's ``backend/src/api/v1/endpoints/training_templates.py`` (create, list,
get, export, import), with delete replaced by archive and revisions added. "Who" comes from the
agent header or the Settings operator name (C5), never from a body.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.agent_origin import Actor, get_actor, requires_approval_when_agent, resolve_who
from ....core.database import get_db
from ....schemas.recipes import (
    RECIPE_FILE_MAX_BYTES,
    CloneIn,
    ImportOut,
    RecipeCreate,
    RecipeList,
    RecipeOut,
    RevisionCreate,
    RevisionOut,
    ValidateIn,
    ValidationOut,
)
from ....schemas.versions import BuildAccepted, RecipeBuildRequest, VersionOut
from ....services import recipe_service
from ....services.agent_label_ledger import build_needs_approval
from ._builds import respond
from ._paging import Page, paging

router = APIRouter(prefix="/api/v1/recipes", tags=["recipes"])


@router.get("", response_model=RecipeList)
async def list_recipes(
    q: str | None = Query(None, max_length=100),
    archived: bool = Query(False),
    page: Page = Depends(paging),
    db: AsyncSession = Depends(get_db),
) -> RecipeList:
    items, total = await recipe_service.list_recipes(
        db, q=q, archived=archived, page=page.page, limit=page.limit
    )
    return RecipeList(items=items, total=total, page=page.page, limit=page.limit)


@router.post("", response_model=RecipeOut, status_code=201)
async def create_recipe(
    body: RecipeCreate,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> RecipeOut:
    who = await resolve_who(actor, db)
    recipe = await recipe_service.create(
        db,
        who,
        name=body.name,
        description=body.description,
        body=body.body,
        step_labels=body.step_labels,
    )
    return await recipe_service.recipe_out(db, recipe)


@router.post("/validate", response_model=ValidationOut)
async def validate_recipe(body: ValidateIn) -> ValidationOut:
    """Validate a body without saving it; every failing step is listed (FR-002.12)."""
    result, _ = recipe_service.validate_body(body.body)
    return result


@router.post("/import", response_model=ImportOut)
async def import_recipe(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> ImportOut:
    who = await resolve_who(actor, db)
    raw = await file.read(RECIPE_FILE_MAX_BYTES + 1)
    return await recipe_service.import_file(db, who, raw)


@router.get("/{recipe_id}", response_model=RecipeOut)
async def get_recipe(recipe_id: str, db: AsyncSession = Depends(get_db)) -> RecipeOut:
    return await recipe_service.recipe_out(db, await recipe_service.get_recipe_row(db, recipe_id))


@router.post("/{recipe_id}/revisions", response_model=RevisionOut, status_code=201)
async def add_revision(
    recipe_id: str,
    body: RevisionCreate,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> RevisionOut:
    who = await resolve_who(actor, db)
    revision = await recipe_service.revise(
        db, who, recipe_id, body=body.body, step_labels=body.step_labels
    )
    return await recipe_service.revision_out(db, revision)


@router.post("/{recipe_id}/clone", response_model=RecipeOut, status_code=201)
async def clone_recipe(
    recipe_id: str,
    body: CloneIn,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> RecipeOut:
    who = await resolve_who(actor, db)
    recipe = await recipe_service.clone(
        db, who, recipe_id, name=body.name, revision_id=body.revision_id
    )
    return await recipe_service.recipe_out(db, recipe)


@router.post("/{recipe_id}/archive", response_model=RecipeOut)
async def archive_recipe(
    recipe_id: str,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> RecipeOut:
    who = await resolve_who(actor, db)
    return await recipe_service.recipe_out(db, await recipe_service.archive(db, who, recipe_id))


@router.get(
    "/{recipe_id}/revisions/{revision_id}/export",
    response_class=Response,
    responses={200: {"content": {"application/json": {}}}},
)
async def export_recipe(
    recipe_id: str, revision_id: str, db: AsyncSession = Depends(get_db)
) -> Response:
    """The canonical file bytes, served as-is: the browser downloads the blob, never re-serialises
    it, so the recipe hash inside describes the file (FR-002.15)."""
    exported = await recipe_service.export(db, recipe_id, revision_id)
    return Response(
        content=exported.content,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{exported.filename}"',
            "X-Recipe-Hash": exported.recipe_hash,
        },
    )


@router.post(
    "/{recipe_id}/build",
    response_model=VersionOut | BuildAccepted,
    status_code=202,
    responses={200: {"model": VersionOut}, 202: {"model": BuildAccepted}},
)
@requires_approval_when_agent("agent_label_rows", when=build_needs_approval)
async def build_from_recipe(
    recipe_id: str,
    body: RecipeBuildRequest,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> Any:
    """Build a version from this recipe's head revision (FR-002.16); delegates to POST /versions."""
    recipe = await recipe_service.get_recipe_row(db, recipe_id)
    request = body.to_build_request(recipe.head_revision_id)
    return await respond(db, actor, request)
