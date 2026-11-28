"""Recipe drafts and save-as-revision (FR-002.18, FR-002.48; FTID 002 section 3.3).

Drafts are the only mutable recipe data and may hold an invalid body, so the guided flow and the
editor can save as the user goes. Saving a draft as a revision runs the full validation of
FR-002.12 and refuses an invalid draft with every failing step listed.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.agent_origin import Who
from ..core.clock import utc_now
from ..core.errors import AppError, NotFoundError
from ..models.recipe import RecipeDraft, RecipeRevision
from ..schemas.drafts import DraftOut
from . import recipe_service


def _uuid(value: str | None, code: str) -> str | None:
    if value is None:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        raise NotFoundError(f"{value!r} is not a valid identifier.", code=code) from None


def out(row: RecipeDraft) -> DraftOut:
    return DraftOut(
        id=row.id,
        recipe_id=row.recipe_id,
        dataset_id=row.dataset_id,
        name=row.name,
        body=dict(row.body),
        step_labels=list(row.step_labels),
        inputs=list(row.inputs),
        flow_state=dict(row.flow_state),
        updated_by=row.updated_by,
        updated_by_origin=row.updated_by_origin,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def get(db: AsyncSession, draft_id: str) -> RecipeDraft:
    row = await db.get(RecipeDraft, _uuid(draft_id, "draft_not_found"), populate_existing=True)
    if row is None:
        raise NotFoundError(f"No draft {draft_id}.", code="draft_not_found")
    return row


def _apply(row: RecipeDraft, fields: dict[str, Any], who: Who) -> None:
    row.recipe_id = _uuid(fields.get("recipe_id"), "recipe_not_found")
    row.dataset_id = _uuid(fields.get("dataset_id"), "dataset_not_found")
    row.name = fields.get("name")
    row.body = fields.get("body") or {}
    row.step_labels = list(fields.get("step_labels") or [])
    row.inputs = list(fields.get("inputs") or [])
    row.flow_state = dict(fields.get("flow_state") or {})
    row.updated_by = who.who
    row.updated_by_origin = who.origin
    row.updated_at = utc_now()


async def create(db: AsyncSession, who: Who, fields: dict[str, Any]) -> RecipeDraft:
    row = RecipeDraft(id=str(uuid.uuid4()), created_at=utc_now())
    _apply(row, fields, who)
    db.add(row)
    await db.commit()
    return row


async def update(db: AsyncSession, who: Who, draft_id: str, fields: dict[str, Any]) -> RecipeDraft:
    row = await get(db, draft_id)
    _apply(row, fields, who)
    await db.commit()
    return row


async def delete(db: AsyncSession, draft_id: str) -> None:
    row = await get(db, draft_id)
    await db.delete(row)
    await db.commit()


async def list_drafts(db: AsyncSession, dataset_id: str | None = None) -> list[RecipeDraft]:
    query = select(RecipeDraft).order_by(RecipeDraft.updated_at.desc()).limit(200)
    if dataset_id:
        query = query.where(RecipeDraft.dataset_id == _uuid(dataset_id, "dataset_not_found"))
    return list((await db.execute(query)).scalars())


async def save_as_revision(
    db: AsyncSession, who: Who, draft_id: str, recipe_name: str | None
) -> RecipeRevision:
    """Validate the draft and save it: a new revision of its recipe, or a new recipe."""
    row = await get(db, draft_id)
    if row.recipe_id is not None:
        revision = await recipe_service.revise(
            db, who, row.recipe_id, body=dict(row.body), step_labels=list(row.step_labels)
        )
    else:
        name = recipe_name or row.name
        if not name:
            raise AppError(
                "Name the recipe before saving the draft (recipe_name).",
                code="recipe_name_required",
                status_code=422,
            )
        recipe = await recipe_service.create(
            db,
            who,
            name=name,
            description=None,
            body=dict(row.body),
            step_labels=list(row.step_labels),
        )
        row = await get(db, draft_id)
        row.recipe_id = recipe.id
        row.updated_at = utc_now()
        await db.commit()
        assert recipe.head_revision_id is not None
        revision = await recipe_service.get_revision_row(db, recipe.head_revision_id)
    return revision
