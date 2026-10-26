"""Recipes: validate, save, revise, clone, archive, export, import (FR-002.10–002.17).

FTID 002 section 3.3. What it guarantees:
- A revision is saved only when every step's operator exists at the named version, is allowlisted,
  and its parameters validate (FR-002.12). Every failing step is reported together; a missing
  version is never replaced by a newer one (T-11 names the current one and offers a clone).
- The recipe hash is the SHA-256 of the canonical body bytes; bodies are content-addressed and
  insert-only, so equal bodies share one row (FR-002.11).
- An export's bytes are the canonical JSON of the stored body plus its name, description and
  labels: the file carries a hash that describes it (FR-002.15).

Services import no FastAPI (ADR section 3.1).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.agent_origin import Who
from ..core.canonical_json import canonical_json
from ..core.clock import utc_now
from ..core.config import get_settings
from ..core.errors import AppError, ConflictError, NotFoundError
from ..models.recipe import Recipe, RecipeBody, RecipeRevision
from ..models.version import Version
from ..schemas.recipes import (
    MAX_STEPS,
    RECIPE_FILE_FORMAT,
    RECIPE_FILE_MAX_BYTES,
    ImportOut,
    RecipeOut,
    RecipeSummary,
    RevisionOut,
    StepError,
    StepValidation,
    ValidationOut,
)
from ..schemas.recipes import RecipeBody as RecipeBodySchema
from . import step_params
from .identity import canonical_body, recipe_hash
from .operator_port import OperatorRefusal, registry
from .step_params import mapping_lookup, needs_resolution, resolve_step


class RecipeInvalid(AppError):
    status_code = 422
    code = "recipe_invalid"

    def __init__(self, result: ValidationOut) -> None:
        failing = [s.index for s in result.steps if s.errors]
        super().__init__(
            "The recipe is not runnable: "
            + (f"steps {failing} have problems" if failing else "the body is malformed")
            + ". Fix each listed step, or save it as a draft and come back to it.",
            details=result.model_dump(mode="json"),
        )


# --------------------------------------------------------------------------------------------
# Validation (FR-002.12)
# --------------------------------------------------------------------------------------------


def step_columns(
    reg: Any, operator: str, version: str, params: dict[str, Any], before: set[str]
) -> tuple[list[str], list[str], list[str]]:
    """``(reads, writes, removes)`` of one step over the columns ``before`` it.

    A native operator that names its columns in parameters says so through ``columns_for`` (and
    ``removes`` for drop_columns); a Data Designer step writes its ``output_column``; otherwise
    the manifest's declared input and output columns. Before the end-to-end run (2026-10-21) only the
    manifest was read, so a parameter-named column and a designer output were invisible here."""
    info = reg.get(operator, version)
    entry = getattr(reg, "entry", None)
    impl = None
    extra: dict[str, Any] = {}
    if callable(entry):
        try:
            found = entry(operator, version)
            impl, extra = getattr(found, "impl", None), dict(getattr(found, "extra", {}) or {})
        except Exception:  # noqa: BLE001 - an unknown operator is reported by the caller
            impl = None
    columns_for = getattr(impl, "columns_for", None)
    if callable(columns_for):
        reads, writes, removes = columns_for(params)
        removes_fn = getattr(impl, "removes", None)
        if callable(removes_fn):
            removes = removes_fn(params, sorted(before))
        return list(reads), list(writes), list(removes)
    if "column_class" in extra:  # a Data Designer step: its reads are its templates' names
        manifest = getattr(found, "manifest", None)
        default = None
        if manifest is not None:
            default = manifest.params_schema["properties"]["output_column"].get("default")
        return [], [str(params.get("output_column") or default)], []
    return list(info.input_columns), list(info.output_columns), []


def validate_body(
    raw: dict[str, Any],
    available_columns: set[str] | None = None,
    columns_seen: list[set[str] | None] | None = None,
    *,
    rule_sets: Mapping[tuple[str, int], tuple[str, str]] | None = None,
) -> tuple[ValidationOut, RecipeBodySchema | None]:
    """Validate a body against the shape and feature 003's registry; collect EVERY error.

    ``available_columns`` is the input's column set when known (at build time, or given); then
    each step's input columns must exist where the step runs, and its outputs become available.
    ``columns_seen`` receives, per step, the columns that step will see (None when unknown), for
    the Data Designer template check.

    ``rule_sets`` (feature 016; FTID 016 section 3.6, I1): the stored records the body's pinned steps
    name, preloaded by the async caller (``step_params.preload``). A step whose operator fills its
    parameters from a stored record (``rule_set@1``) is resolved with them, so its pin is checked and
    its output columns are known downstream; with ``rule_sets=None`` such a step is reported
    ``rule_set_not_resolved``, never passed silently. ``RECIPE_MAX_RULE_SETS`` caps the distinct
    pins.
    """
    steps_raw = raw.get("steps") if isinstance(raw, dict) else None
    if isinstance(steps_raw, list) and len(steps_raw) > MAX_STEPS:
        return (
            ValidationOut(
                valid=False,
                body_errors=[
                    StepError(
                        code="too_many_steps",
                        rule="max_steps",
                        message=f"The recipe has {len(steps_raw)} steps; a recipe holds at most "
                        f"{MAX_STEPS}. Split it into recipes chained version -> version, or use "
                        "the native operators that replace long template chains "
                        "(checksum_validate, phrase_proximity, rule_label).",
                    )
                ],
            ),
            None,
        )
    try:
        body = RecipeBodySchema.model_validate(raw)
    except ValidationError as exc:
        problems = [
            StepError(
                code="body_invalid",
                message=f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}",
            )
            for err in exc.errors()
        ]
        return ValidationOut(valid=False, body_errors=problems), None

    reg = registry()
    columns = set(available_columns) if available_columns is not None else None
    steps: list[StepValidation] = []
    from .rules.step_resolution import pins_a_rule_set

    pins = {
        (str(s.params.get("rule_set_name")), s.params.get("rule_set_version"))
        for s in body.steps
        if needs_resolution({"operator": s.operator})
        and pins_a_rule_set({"operator": s.operator, "params": s.params})
    }
    cap = get_settings().recipe_max_rule_sets
    if len(pins) > cap:
        return (
            ValidationOut(
                valid=False,
                body_errors=[
                    StepError(
                        code="cap_exceeded",
                        rule="RECIPE_MAX_RULE_SETS",
                        message=f"The recipe pins {len(pins)} rule-set versions; "
                        f"RECIPE_MAX_RULE_SETS allows {cap}. Combine rule sets or split the "
                        "recipe.",
                    )
                ],
            ),
            body,
        )
    for index, step in enumerate(body.steps, start=1):
        errors: list[StepError] = []
        try:
            info = reg.get(step.operator, step.version)
        except OperatorRefusal as refusal:
            current = reg.current_version(step.operator)
            if current is not None and current != step.version:
                errors.append(
                    StepError(
                        code="version_unavailable",
                        message=(
                            f"This recipe pins {step.operator} {step.version}, which is not "
                            f'installed. Install it, or use "Clone recipe with current '
                            f'operators" to move to {step.operator} {current}.'
                        ),
                    )
                )
            else:
                errors.append(StepError(code=refusal.code, message=refusal.message))
            if columns_seen is not None:
                columns_seen.append(None)
            columns = None  # an unknown step's outputs are unknown: stop checking columns
            steps.append(
                StepValidation(
                    index=index, operator=step.operator, version=step.version, errors=errors
                )
            )
            continue
        if not reg.is_allowed(step.operator, step.version):
            errors.append(
                StepError(
                    code="operator_not_allowed",
                    message=(
                        f"{step.operator} {step.version} is installed but not allowlisted. "
                        "Allow it on the Operators screen, or choose another operator."
                    ),
                )
            )
        params = dict(step.params)
        for problem in reg.validate_params(step.operator, step.version, step.params):
            errors.append(StepError(code="params_invalid", message=problem))
        if (
            not errors
            and needs_resolution({"operator": step.operator})
            and pins_a_rule_set({"operator": step.operator, "params": params})
        ):
            if rule_sets is None:
                errors.append(
                    StepError(
                        code="rule_set_not_resolved",
                        message=f"Step {index} pins a stored rule set that this check did not "
                        "load; validate through the recipe routes, which load it.",
                    )
                )
            else:
                try:
                    params = resolve_step(
                        {"operator": step.operator, "params": params}, mapping_lookup(rule_sets)
                    )["params"]
                except AppError as refusal:
                    errors.append(StepError(code=refusal.code, message=refusal.message))
        if columns_seen is not None:
            columns_seen.append(set(columns) if columns is not None else None)
        if columns is not None:
            try:
                reads, writes, removes = step_columns(
                    reg, step.operator, step.version, params, columns
                )
            except Exception:  # noqa: BLE001 - a malformed parameter is reported above
                reads, writes, removes = list(info.input_columns), list(info.output_columns), []
            for column in [c for c in reads if c not in columns]:
                errors.append(
                    StepError(
                        code="missing_input_column",
                        message=(
                            f"Step {index} reads column {column!r}, which does not exist at "
                            f"this point in the recipe. It has: {', '.join(sorted(columns))}."
                        ),
                    )
                )
            columns.difference_update(removes)
            columns.update(writes)
        steps.append(
            StepValidation(index=index, operator=step.operator, version=step.version, errors=errors)
        )
    valid = all(not s.errors for s in steps)
    out = ValidationOut(valid=valid, steps=steps)
    if available_columns is not None:
        out.columns_checked = "given"
    return out, body


async def preload_rule_sets(db: AsyncSession, raw: Any) -> dict[tuple[str, int], tuple[str, str]]:
    """The stored records a body's pinned steps name (feature 016), for ``validate_body``."""
    steps = raw.get("steps") if isinstance(raw, dict) else None
    if not isinstance(steps, list):
        return {}
    return await step_params.preload(db, [s for s in steps if isinstance(s, dict)])


def require_valid(
    raw: dict[str, Any],
    available_columns: set[str] | None = None,
    *,
    rule_sets: Mapping[tuple[str, int], tuple[str, str]] | None = None,
) -> RecipeBodySchema:
    result, body = validate_body(raw, available_columns, rule_sets=rule_sets)
    if not result.valid or body is None:
        raise RecipeInvalid(result)
    return body


# --------------------------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------------------------


async def save_body(db: AsyncSession, body: RecipeBodySchema) -> str:
    """Insert the canonical body once; return its hash (content-addressed, insert-only)."""
    as_dict = body.model_dump(mode="json")
    digest = recipe_hash(as_dict)
    await db.execute(
        pg_insert(RecipeBody)
        .values(hash=digest, canonical=canonical_body(as_dict), body=as_dict)
        .on_conflict_do_nothing(index_elements=["hash"])
    )
    return digest


def _check_labels(labels: Sequence[str], body: RecipeBodySchema) -> list[str]:
    if labels and len(labels) != len(body.steps):
        raise AppError(
            f"step_labels has {len(labels)} entries for {len(body.steps)} steps; give one label "
            "per step or none.",
            code="recipe_labels_mismatch",
            status_code=422,
        )
    return [label[:120] for label in labels]


async def _name_free(db: AsyncSession, name: str) -> None:
    taken = (await db.execute(select(Recipe.id).where(Recipe.name == name))).scalar_one_or_none()
    if taken is not None:
        raise ConflictError(
            f"A recipe named {name!r} already exists. Choose another name, or add a revision to "
            "the existing recipe.",
            code="recipe_name_taken",
            details={"recipe_id": taken},
        )


async def get_recipe_row(db: AsyncSession, recipe_id: str) -> Recipe:
    row = await db.get(Recipe, _uuid(recipe_id, "recipe_not_found"), populate_existing=True)
    if row is None:
        raise NotFoundError(f"No recipe {recipe_id}.", code="recipe_not_found")
    return row


async def get_revision_row(db: AsyncSession, revision_id: str) -> RecipeRevision:
    row = await db.get(RecipeRevision, _uuid(revision_id, "revision_not_found"))
    if row is None:
        raise NotFoundError(f"No recipe revision {revision_id}.", code="revision_not_found")
    return row


def _uuid(value: str, code: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        raise NotFoundError(f"{value!r} is not a valid identifier.", code=code) from None


async def _add_revision(
    db: AsyncSession,
    recipe: Recipe,
    digest: str,
    labels: list[str],
    who: Who,
    *,
    cloned_from: str | None = None,
    imported: bool = False,
) -> RecipeRevision:
    number = (
        await db.execute(
            select(func.coalesce(func.max(RecipeRevision.revision_number), 0)).where(
                RecipeRevision.recipe_id == recipe.id
            )
        )
    ).scalar_one() + 1
    revision = RecipeRevision(
        id=str(uuid.uuid4()),
        recipe_id=recipe.id,
        revision_number=int(number),
        recipe_hash=digest,
        step_labels=labels,
        cloned_from_revision_id=cloned_from,
        imported=imported,
        created_by=who.who,
        created_by_origin=who.origin,
    )
    db.add(revision)
    await db.flush()
    recipe.head_revision_id = revision.id
    recipe.updated_at = utc_now()
    await db.flush()
    return revision


async def create(
    db: AsyncSession,
    who: Who,
    *,
    name: str,
    description: str | None,
    body: dict[str, Any],
    step_labels: Sequence[str] = (),
    behaviour_key: str | None = None,
) -> Recipe:
    parsed = require_valid(body, rule_sets=await preload_rule_sets(db, body))
    labels = _check_labels(step_labels, parsed)
    await _name_free(db, name)
    digest = await save_body(db, parsed)
    recipe = Recipe(
        id=str(uuid.uuid4()),
        name=name,
        description=description,
        behaviour_key=behaviour_key,
        archived=False,
        created_by=who.who,
        created_by_origin=who.origin,
    )
    db.add(recipe)
    await db.flush()
    await _add_revision(db, recipe, digest, labels, who)
    await db.commit()
    return recipe


def _refuse_archived(recipe: Recipe) -> None:
    if recipe.archived:
        raise ConflictError(
            f"Recipe {recipe.name!r} is archived. Clone it to keep working on it.",
            code="recipe_archived",
            details={"recipe_id": recipe.id},
        )


async def revise(
    db: AsyncSession,
    who: Who,
    recipe_id: str,
    *,
    body: dict[str, Any],
    step_labels: Sequence[str] = (),
) -> RecipeRevision:
    recipe = await get_recipe_row(db, recipe_id)
    _refuse_archived(recipe)
    parsed = require_valid(body, rule_sets=await preload_rule_sets(db, body))
    labels = _check_labels(step_labels, parsed)
    digest = await save_body(db, parsed)
    revision = await _add_revision(db, recipe, digest, labels, who)
    await db.commit()
    return revision


async def clone(
    db: AsyncSession,
    who: Who,
    recipe_id: str,
    *,
    name: str,
    revision_id: str | None = None,
    behaviour_key: str | None = None,
    keep_key: bool = True,
) -> Recipe:
    """A new recipe from one revision of another. 017 FR-017.9: the clone keeps the source's
    ``behaviour_key`` while ``keep_key``; else it takes ``behaviour_key`` (None clears it)."""
    source = await get_recipe_row(db, recipe_id)
    revision_id = revision_id or source.head_revision_id
    if revision_id is None:
        raise ConflictError("The recipe has no revision to clone.", code="revision_not_found")
    revision = await get_revision_row(db, revision_id)
    if revision.recipe_id != source.id:
        raise NotFoundError(
            f"Revision {revision_id} does not belong to recipe {recipe_id}.",
            code="revision_not_found",
        )
    await _name_free(db, name)
    recipe = Recipe(
        id=str(uuid.uuid4()),
        name=name,
        description=source.description,
        behaviour_key=source.behaviour_key if keep_key else behaviour_key,
        archived=False,
        created_by=who.who,
        created_by_origin=who.origin,
    )
    db.add(recipe)
    await db.flush()
    await _add_revision(
        db, recipe, revision.recipe_hash, list(revision.step_labels), who, cloned_from=revision.id
    )
    await db.commit()
    return recipe


async def archive(db: AsyncSession, who: Who, recipe_id: str) -> Recipe:
    recipe = await get_recipe_row(db, recipe_id)
    if not recipe.archived:
        recipe.archived = True
        recipe.archived_at = utc_now()
        recipe.archived_by = who.who
        recipe.archived_by_origin = who.origin
        # Set here, not by the column's onupdate: a server-side value expires the attribute, and
        # the response would then lazy-load it outside the async context.
        recipe.updated_at = utc_now()
        await db.commit()
    return recipe


# --------------------------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------------------------


async def body_of(db: AsyncSession, digest: str) -> dict[str, Any]:
    """The stored body, parsed from its canonical bytes (never from the JSONB query copy)."""
    row = await db.get(RecipeBody, digest)
    if row is None:
        raise NotFoundError(f"No recipe body {digest}.", code="recipe_body_not_found")
    parsed: dict[str, Any] = json.loads(row.canonical)
    return parsed


MAX_INCLUDE_DEPTH = 3


async def resolve_body(
    db: AsyncSession, body: dict[str, Any] | None, revision_id: str | None, *, depth: int = 0
) -> dict[str, Any]:
    """The recipe body a request means: given inline, or a saved revision's by reference, with
    every ``{"include": "<revision id>", "from": i, "to": j}`` step replaced by that revision's
    steps i..j (1-based, inclusive; all when omitted). e2e run G6 (2026-10-21): 120 KB of recipe
    JSON was pasted inline and a shared validator chain duplicated into 12 recipes. The stored
    body is the EXPANDED one, so its hash still describes exactly the steps that run."""
    if (body is None) == (revision_id is None):
        raise AppError(
            "Give the recipe body, or recipe_revision_id to use a saved revision - one of them.",
            code="recipe_body_ambiguous",
            status_code=422,
        )
    if revision_id is not None:
        revision = await get_revision_row(db, revision_id)
        return await body_of(db, revision.recipe_hash)
    assert body is not None
    steps = body.get("steps")
    if not isinstance(steps, list) or not any(
        isinstance(s, dict) and "include" in s for s in steps
    ):
        return body
    if depth >= MAX_INCLUDE_DEPTH:
        raise AppError(
            f"Includes nest more than {MAX_INCLUDE_DEPTH} deep.",
            code="recipe_include_invalid",
            status_code=422,
        )
    expanded: list[Any] = []
    for position, step in enumerate(steps, start=1):
        if not (isinstance(step, dict) and "include" in step):
            expanded.append(step)
            continue
        extra = set(step) - {"include", "from", "to"}
        if extra or not isinstance(step["include"], str):
            raise AppError(
                f"Step {position}: an include is {{include: <revision id>, from?, to?}}; "
                f"{sorted(extra) or 'include must be a revision id'} is not.",
                code="recipe_include_invalid",
                status_code=422,
            )
        included = await resolve_body(db, None, step["include"], depth=depth + 1)
        block = list(included.get("steps") or [])
        start = int(step.get("from") or 1)
        end = int(step.get("to") or len(block))
        if not 1 <= start <= end <= len(block):
            raise AppError(
                f"Step {position}: steps {start}..{end} are outside the included revision's "
                f"1..{len(block)}.",
                code="recipe_include_invalid",
                status_code=422,
            )
        expanded.extend(block[start - 1 : end])
    return {**body, "steps": expanded}


async def saved_summary(
    db: AsyncSession, recipe: Recipe, revision: RecipeRevision
) -> dict[str, Any]:
    """What a save answers when the body is not echoed: ids, number, hash and a step census."""
    body = await body_of(db, revision.recipe_hash)
    operators: dict[str, int] = {}
    for step in body.get("steps", []):
        key = f"{step['operator']}@{step['version']}"
        operators[key] = operators.get(key, 0) + 1
    return {
        "recipe_id": recipe.id,
        "name": recipe.name,
        "revision_id": revision.id,
        "revision_number": revision.revision_number,
        "recipe_hash": revision.recipe_hash,
        "step_count": len(body.get("steps", [])),
        "operators": operators,
        "body_bytes": len(canonical_body(body)),
        "note": "The body is not echoed; read it with dataworks_get_recipe.",
    }


async def _versions_built(db: AsyncSession, revision_ids: Sequence[str]) -> dict[str, int]:
    if not revision_ids:
        return {}
    rows = await db.execute(
        select(Version.recipe_revision_id, func.count())
        .where(Version.recipe_revision_id.in_(list(revision_ids)))
        .group_by(Version.recipe_revision_id)
    )
    return {str(rid): int(n) for rid, n in rows.all()}


def _providers(body: dict[str, Any]) -> list[str]:
    reg = registry()
    providers: set[str] = set()
    for step in body.get("steps", []):
        try:
            providers.add(reg.get(step["operator"], step["version"]).provider)
        except OperatorRefusal:
            providers.add("not installed")
    return sorted(providers)


async def revision_out(
    db: AsyncSession, revision: RecipeRevision, built: dict[str, int] | None = None
) -> RevisionOut:
    built = built if built is not None else await _versions_built(db, [revision.id])
    return RevisionOut(
        id=revision.id,
        recipe_id=revision.recipe_id,
        revision_number=revision.revision_number,
        recipe_hash=revision.recipe_hash,
        body=await body_of(db, revision.recipe_hash),
        step_labels=list(revision.step_labels),
        cloned_from_revision_id=revision.cloned_from_revision_id,
        imported=revision.imported,
        created_by=revision.created_by,
        created_by_origin=revision.created_by_origin,
        created_at=revision.created_at,
        versions_built=built.get(revision.id, 0),
    )


async def _summary(db: AsyncSession, recipe: Recipe) -> tuple[RecipeSummary, list[RecipeRevision]]:
    revisions = list(
        (
            await db.execute(
                select(RecipeRevision)
                .where(RecipeRevision.recipe_id == recipe.id)
                .order_by(RecipeRevision.revision_number)
            )
        ).scalars()
    )
    built = await _versions_built(db, [r.id for r in revisions])
    head = next((r for r in revisions if r.id == recipe.head_revision_id), None)
    head_body = await body_of(db, head.recipe_hash) if head else {"steps": []}
    summary = RecipeSummary(
        id=recipe.id,
        name=recipe.name,
        description=recipe.description,
        archived=recipe.archived,
        head_revision_id=recipe.head_revision_id,
        head_hash=head.recipe_hash if head else None,
        step_count=len(head_body.get("steps", [])),
        providers=_providers(head_body),
        revision_count=len(revisions),
        versions_built=sum(built.values()),
        created_by=recipe.created_by,
        created_at=recipe.created_at,
        updated_at=recipe.updated_at,
        behaviour_key=recipe.behaviour_key,
    )
    return summary, revisions


async def recipe_out(db: AsyncSession, recipe: Recipe) -> RecipeOut:
    summary, revisions = await _summary(db, recipe)
    built = await _versions_built(db, [r.id for r in revisions])
    return RecipeOut(
        **summary.model_dump(),
        revisions=[await revision_out(db, r, built) for r in revisions],
        archived_at=recipe.archived_at,
        archived_by=recipe.archived_by,
    )


async def list_recipes(
    db: AsyncSession, *, q: str | None, archived: bool, page: int, limit: int
) -> tuple[list[RecipeSummary], int]:
    query = select(Recipe)
    if not archived:
        query = query.where(Recipe.archived.is_(False))
    if q:
        query = query.where(Recipe.name.ilike(f"%{q}%"))
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = (
        await db.execute(
            query.order_by(Recipe.updated_at.desc()).offset((page - 1) * limit).limit(limit)
        )
    ).scalars()
    return [(await _summary(db, r))[0] for r in rows], int(total)


# --------------------------------------------------------------------------------------------
# Export and import (FR-002.15)
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ExportFile:
    filename: str
    content: bytes
    recipe_hash: str


async def export(db: AsyncSession, recipe_id: str, revision_id: str) -> ExportFile:
    recipe = await get_recipe_row(db, recipe_id)
    revision = await get_revision_row(db, revision_id)
    if revision.recipe_id != recipe.id:
        raise NotFoundError(
            f"Revision {revision_id} does not belong to recipe {recipe_id}.",
            code="revision_not_found",
        )
    document = {
        "format": RECIPE_FILE_FORMAT,
        "name": recipe.name,
        "description": recipe.description,
        "step_labels": list(revision.step_labels),
        "recipe_hash": revision.recipe_hash,
        "body": await body_of(db, revision.recipe_hash),
    }
    return ExportFile(
        filename=f"{recipe.name}.recipe.json",
        content=canonical_json(document),
        recipe_hash=revision.recipe_hash,
    )


def _refuse(reasons: list[StepError], digest: str | None = None) -> ImportOut:
    return ImportOut(outcome="refused", hash=digest, reasons=reasons)


async def import_file(db: AsyncSession, who: Who, raw: bytes) -> ImportOut:
    """Import a recipe file: validate, recompute the hash from canonical bytes, and report."""
    if len(raw) > RECIPE_FILE_MAX_BYTES:
        raise AppError(
            f"The recipe file is {len(raw):,} bytes; the limit is {RECIPE_FILE_MAX_BYTES:,}.",
            code="recipe_import_invalid",
            status_code=413,
        )
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AppError(
            f"The file is not JSON ({exc}). Export the recipe from miDataworks and try again.",
            code="recipe_import_invalid",
            status_code=422,
        ) from None
    if not isinstance(document, dict) or document.get("format") != RECIPE_FILE_FORMAT:
        raise AppError(
            f"The file is not a {RECIPE_FILE_FORMAT} recipe file.",
            code="recipe_import_invalid",
            status_code=422,
        )
    name = document.get("name")
    body = document.get("body")
    if not isinstance(name, str) or not name or len(name) > 100 or not isinstance(body, dict):
        raise AppError(
            "The recipe file needs a name (1-100 characters) and a body.",
            code="recipe_import_invalid",
            status_code=422,
        )
    notes: list[str] = []
    if canonical_json(document) != raw:
        notes.append("file_not_canonical: the file was re-serialised; its hash was recomputed")
    result, parsed = validate_body(body, rule_sets=await preload_rule_sets(db, body))
    if parsed is None:
        return _refuse(result.body_errors)
    digest = recipe_hash(parsed.model_dump(mode="json"))
    claimed = document.get("recipe_hash")
    if claimed != digest:
        return _refuse(
            [
                StepError(
                    code="hash_mismatch",
                    message=(
                        f"The file says its recipe hash is {claimed}; its body hashes to {digest}. "
                        "The file was edited after export."
                    ),
                )
            ],
            digest,
        )
    if not result.valid:
        return _refuse([e for s in result.steps for e in s.errors], digest)
    labels_raw = document.get("step_labels") or []
    if not isinstance(labels_raw, list) or not all(isinstance(x, str) for x in labels_raw):
        return _refuse([StepError(code="labels_invalid", message="step_labels must be strings")])
    labels = _check_labels(labels_raw, parsed)

    existing = (await db.execute(select(Recipe).where(Recipe.name == name))).scalar_one_or_none()
    if existing is not None:
        same = (
            await db.execute(
                select(RecipeRevision.id).where(
                    RecipeRevision.recipe_id == existing.id, RecipeRevision.recipe_hash == digest
                )
            )
        ).first()
        if same is not None:
            return ImportOut(
                outcome="already_present",
                hash=digest,
                recipe=await recipe_out(db, existing),
                notes=notes,
            )
        _refuse_archived(existing)
        await save_body(db, parsed)
        await _add_revision(db, existing, digest, labels, who, imported=True)
        await db.commit()
        notes.append(f"added as a new revision of the existing recipe {name!r}")
        return ImportOut(
            outcome="created", hash=digest, recipe=await recipe_out(db, existing), notes=notes
        )
    await save_body(db, parsed)
    description = document.get("description")
    recipe = Recipe(
        id=str(uuid.uuid4()),
        name=name,
        description=description if isinstance(description, str) else None,
        archived=False,
        created_by=who.who,
        created_by_origin=who.origin,
    )
    db.add(recipe)
    await db.flush()
    await _add_revision(db, recipe, digest, labels, who, imported=True)
    await db.commit()
    return ImportOut(
        outcome="created", hash=digest, recipe=await recipe_out(db, recipe), notes=notes
    )
