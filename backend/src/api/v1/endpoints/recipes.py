"""Recipes REST (FR-002.13–002.17, FR-002.45; FTDD 002 section 5.2).

Shape follows miStudio's ``backend/src/api/v1/endpoints/training_templates.py`` (create, list,
get, export, import), with delete replaced by archive and revisions added. "Who" comes from the
agent header or the Settings operator name (C5), never from a body.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
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
    RecipeSaved,
    RevisionCreate,
    RevisionOut,
    StepError,
    ValidateIn,
    ValidationOut,
)
from ....schemas.versions import BuildAccepted, RecipeBuildRequest, VersionOut
from ....services import recipe_service
from ....services.agent_label_ledger import build_needs_approval
from ._builds import respond
from ._paging import Page, behaviour_filter, paging

router = APIRouter(prefix="/api/v1/recipes", tags=["recipes"])


@router.get("", response_model=RecipeList)
async def list_recipes(
    q: str | None = Query(None, max_length=100),
    archived: bool = Query(False),
    page: Page = Depends(paging),
    behaviour_key: str | None = Depends(behaviour_filter),
    db: AsyncSession = Depends(get_db),
) -> RecipeList:
    items, total = await recipe_service.list_recipes(
        db,
        q=q,
        archived=archived,
        page=page.page,
        limit=page.limit,
        behaviour_key=behaviour_key,
    )
    return RecipeList(items=items, total=total, page=page.page, limit=page.limit)


@router.post("", response_model=RecipeOut | RecipeSaved, status_code=201)
async def create_recipe(
    body: RecipeCreate,
    view: Literal["full", "summary"] = Query("full"),
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> RecipeOut | RecipeSaved:
    """Create a recipe. ``view=summary`` answers ids, hash and a step census instead of echoing
    the body (e2e run G6)."""
    who = await resolve_who(actor, db)
    raw = await recipe_service.resolve_body(db, body.body, body.recipe_revision_id)
    recipe = await recipe_service.create(
        db,
        who,
        name=body.name,
        description=body.description,
        body=raw,
        step_labels=body.step_labels,
        behaviour_key=body.behaviour_key,
    )
    if view == "summary":
        revision = await recipe_service.get_revision_row(db, str(recipe.head_revision_id))
        return RecipeSaved(**await recipe_service.saved_summary(db, recipe, revision))
    return await recipe_service.recipe_out(db, recipe)


def with_designer_rules(
    result: ValidationOut,
    raw: dict[str, Any],
    columns_seen: list[set[str] | None] | None = None,
) -> ValidationOut:
    """Add Data Designer's template-rule failures to each ``dd_*`` step that is otherwise valid
    (PII run G4), each naming its rule (``too_deep``, ``too_many_nodes``, ``unknown_reference``
    ...). With ``columns_seen`` every template is checked against the columns THAT step will see
    - the real input columns plus earlier steps' outputs - so an unknown reference is caught
    before a build (e2e run G2/G7: plan_build said "valid" and the build failed on depth 13).
    A worker that does not answer is a step error ``designer_unavailable``: an unchecked template
    is never reported valid."""
    from ....operators.data_designer import validation
    from ....operators.errors import OperatorError
    from ....operators.registry import current

    reg = current()
    raw_steps = list(raw.get("steps") or [])
    for step in result.steps:
        if step.errors or step.operator is None or step.version is None:
            continue
        entry = reg.entry(step.operator, step.version)
        if not validation.is_designer(entry):
            continue
        params = dict((raw_steps[step.index - 1] or {}).get("params") or {})
        seen = None
        if columns_seen is not None and step.index - 1 < len(columns_seen):
            visible = columns_seen[step.index - 1]
            seen = sorted(visible) if visible is not None else None
        try:
            answer = validation.check_templates(entry, params, seen)
        except OperatorError as exc:
            step.errors.append(StepError(code=exc.code, message=exc.message))
            continue
        if answer.get("limits"):
            limits = dict(answer["limits"])
            result.limits.update(
                {
                    "designer_max_depth": limits.get("max_depth"),
                    "designer_max_nodes": limits.get("max_nodes"),
                    "designer_filters": limits.get("filters"),
                }
            )
        for problem in answer.get("errors") or []:
            step.errors.append(
                StepError(
                    code="template_invalid",
                    rule=problem.get("rule"),
                    message=f"{problem.get('field') or 'config'}: {problem.get('message')}",
                )
            )
    result.valid = not result.body_errors and all(not s.errors for s in result.steps)
    return result


@router.post("/validate", response_model=ValidationOut)
async def validate_recipe(body: ValidateIn, db: AsyncSession = Depends(get_db)) -> ValidationOut:
    """Validate a body - or a saved revision, by ``recipe_revision_id`` - without saving; every
    failing step is listed (FR-002.12) and the limits are stated. With ``columns`` (the input's
    columns), each step's column references and each Data Designer template are checked against
    what that step will see. A Data Designer step whose shape is valid is also checked against
    Data Designer's own template rules by the designer worker (PII run G4)."""
    raw = await recipe_service.resolve_body(db, body.body, body.recipe_revision_id)
    seen: list[set[str] | None] = []
    columns = set(body.columns) if body.columns is not None else None
    result, _ = recipe_service.validate_body(
        raw,
        columns,
        seen if columns is not None else None,
        rule_sets=await recipe_service.preload_rule_sets(db, raw),
    )
    return await run_in_threadpool(
        with_designer_rules, result, raw, seen if columns is not None else None
    )


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


@router.post("/{recipe_id}/revisions", response_model=RevisionOut | RecipeSaved, status_code=201)
async def add_revision(
    recipe_id: str,
    body: RevisionCreate,
    view: Literal["full", "summary"] = Query("full"),
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> RevisionOut | RecipeSaved:
    who = await resolve_who(actor, db)
    raw = await recipe_service.resolve_body(db, body.body, body.recipe_revision_id)
    revision = await recipe_service.revise(
        db, who, recipe_id, body=raw, step_labels=body.step_labels
    )
    if view == "summary":
        recipe = await recipe_service.get_recipe_row(db, recipe_id)
        return RecipeSaved(**await recipe_service.saved_summary(db, recipe, revision))
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
        db,
        who,
        recipe_id,
        name=body.name,
        revision_id=body.revision_id,
        behaviour_key=body.behaviour_key,
        keep_key="behaviour_key" not in body.model_fields_set,
        substitutions=[s.model_dump() for s in body.substitutions],
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
