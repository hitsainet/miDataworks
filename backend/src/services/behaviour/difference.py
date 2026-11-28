"""The structured difference between a source and its clone (017 FR-017.59; FTID 017 §11).

Recipes and generation templates are compared as ``{behaviour_key, body}`` (a recipe's body,
parsed from its stored canonical bytes: its head's for a recipe id, that revision's for a revision
id; a template's body and kind) with 016's difference
(``rules/difference.py``: RFC 6901 pointers, ``add``/``remove``/``replace``), so a clone made with
substitutions reads back exactly the fields that were substituted plus the key it moved to. 016
owns the rule-set difference (``dataworks_get_rule_set_difference``).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ...core.errors import AppError, NotFoundError
from ...rules.difference import difference

KINDS = ("recipe", "generation_template")


async def _recipe(db: AsyncSession, given_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """A recipe id means its head revision; a revision id means exactly that revision (FU-7a MCP
    gap (d), 2026-11-27: ``clone_recipe`` takes a ``revision_id``, and a source that moved on
    after the clone was compared at its new head). ``given`` and ``is_head`` say which it was."""
    from .. import recipe_service

    try:
        recipe = await recipe_service.get_recipe_row(db, given_id)
        given, revision_id = "recipe", recipe.head_revision_id
    except NotFoundError:
        try:
            revision = await recipe_service.get_revision_row(db, given_id)
        except NotFoundError:
            raise NotFoundError(
                f"No recipe or recipe revision {given_id}.", code="recipe_not_found"
            ) from None
        recipe = await recipe_service.get_recipe_row(db, str(revision.recipe_id))
        given, revision_id = "revision", revision.id
    if revision_id is None:
        raise AppError(
            f"Recipe {recipe.name!r} has no revision to compare.",
            code="revision_not_found",
            status_code=409,
            details={"next": {"tool": "dataworks_get_recipe", "args": {"recipe_id": recipe.id}}},
        )
    revision = await recipe_service.get_revision_row(db, str(revision_id))
    body = await recipe_service.body_of(db, revision.recipe_hash)
    facts = {
        "id": recipe.id,
        "name": recipe.name,
        "given": given,
        "revision_id": revision.id,
        "revision_number": revision.revision_number,
        "is_head": str(revision.id) == str(recipe.head_revision_id),
        "recipe_hash": revision.recipe_hash,
        "cloned_from_revision_id": revision.cloned_from_revision_id,
    }
    return facts, {"behaviour_key": recipe.behaviour_key, "body": body}


async def _template(db: AsyncSession, template_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    from ..generation import template_service

    row = await template_service.get(db, template_id)
    facts = {
        "id": row.id,
        "name": row.name,
        "version": row.version,
        "content_hash": row.content_hash,
        "cloned_from": row.cloned_from,
    }
    return facts, {"behaviour_key": row.behaviour_key, "kind": row.kind, "body": dict(row.body)}


async def difference_of(db: AsyncSession, kind: str, a: str, b: str) -> dict[str, Any]:
    read = _recipe if kind == "recipe" else _template
    (fa, da), (fb, db_) = await read(db, a), await read(db, b)
    changes = difference(da, db_)
    return {
        "kind": kind,
        "a": fa,
        "b": fb,
        "compared": sorted(da),
        "difference": changes,
        "same": not changes,
    }
