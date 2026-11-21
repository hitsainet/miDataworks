"""Rule-signal routes (feature 016): the validator library (FR-016.5, FR-016.39) and rule sets
(FR-016.16 - FR-016.21).

Validator answers are derived from the LIVE registry (``rules.validators.VALIDATORS``) and the
packaged contract file (``rule-signal-cases-v1.json``), never from a second hand-kept list. Rule-set
work runs in ``services/rules/rule_set_service.py`` on a sync session in the thread pool. None of
these routes is gated (FR-016.35): save writes data but loads no model and calls nothing external.
``validate``, ``preview`` and ``difference`` are declared BEFORE ``/{name}``, or FastAPI would read
them as names.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Path, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.agent_origin import Actor, get_actor, resolve_who
from ....core.database import get_db, get_sync_db
from ....core.errors import AppError
from ....rules.cases import load_cases
from ....rules.next_step import next_for
from ....rules.rule_set import NAME_PATTERN
from ....rules.validators import VALIDATORS, declaration
from ....schemas.archive import ArchiveRequest
from ....schemas.behaviour import BEHAVIOUR_KEY_PATTERN
from ....schemas.rules import RuleSetCloneIn, RuleSetPreviewIn, RuleSetSaveIn, RuleSetValidateIn
from ....services import archive_service
from ....services.rules import rule_set_service as service
from ._paging import Page, paging

validators_router = APIRouter(prefix="/api/v1/validators", tags=["rule_signals"])

VALIDATOR_ID_PATTERN = r"^[a-z][a-z0-9_]{0,47}$"


def _summary(key: tuple[str, int]) -> dict[str, Any]:
    section = load_cases()["validators"].get(f"{key[0]}@{key[1]}", {})
    return {
        **declaration(VALIDATORS[key]),
        "valid_path": section.get("valid_path"),
        "case_count": len(section.get("cases", [])),
    }


@validators_router.get("")
async def list_validators(page: Page = Depends(paging)) -> dict[str, Any]:
    """Every registered validator version, in ``(id, version)`` order, paged."""
    keys = sorted(VALIDATORS)
    return {
        "items": [_summary(k) for k in keys[page.offset : page.offset + page.limit]],
        "page": page.page,
        "limit": page.limit,
        "total": len(keys),
    }


@validators_router.get("/{validator_id}/versions/{version}")
async def get_validator(
    validator_id: str = Path(..., pattern=VALIDATOR_ID_PATTERN),
    version: int = Path(..., ge=1),
) -> dict[str, Any]:
    """One validator version: its declaration and its cases from the contract file."""
    key = (validator_id, version)
    if key not in VALIDATORS:
        raise AppError(
            f"Validator {validator_id}@{version} is not registered. dataworks_list_validators lists "
            "the registered versions.",
            code="validator_not_found",
            status_code=404,
            details={"validator": validator_id, "version": version},
        )
    section = load_cases()["validators"].get(f"{validator_id}@{version}", {})
    return {**_summary(key), "cases": section.get("cases", [])}


# --- rule sets (slice 2) --------------------------------------------------------------------------

rule_sets_router = APIRouter(prefix="/api/v1/rule-sets", tags=["rule_signals"])
NamePath = Path(..., pattern=NAME_PATTERN)


@rule_sets_router.post("", status_code=201)
async def save_rule_set(
    body: RuleSetSaveIn, db: AsyncSession = Depends(get_db), actor: Actor = Depends(get_actor)
) -> dict[str, Any]:
    """Save a rule set: every stage passes or nothing is stored (FR-016.17). Version n+1 of its
    name; a document equal to the latest version stores nothing and answers it (created false)."""
    who = await resolve_who(actor, db)

    def run() -> dict[str, Any]:
        with get_sync_db() as session:
            return service.save(session, body.document, who)

    return await run_in_threadpool(run)


@rule_sets_router.post("/validate")
async def validate_rule_set(body: RuleSetValidateIn) -> dict[str, Any]:
    """Every problem and every failing case of a document (inline or stored); writes nothing."""

    def run() -> dict[str, Any]:
        with get_sync_db() as session:
            return service.validate(session, body.document, body.name, body.version)

    return await run_in_threadpool(run)


@rule_sets_router.post("/preview")
async def preview_rule_set(body: RuleSetPreviewIn) -> dict[str, Any]:
    """One text through one document; the text is never stored or logged (FR-016.19)."""

    def run() -> dict[str, Any]:
        with get_sync_db() as session:
            return service.preview(
                session, body.text, body.document, body.name, body.version, body.element
            )

    return await run_in_threadpool(run)


@rule_sets_router.get("/difference")
async def rule_set_difference(
    a: str = Query(..., max_length=100), b: str = Query(..., max_length=100)
) -> dict[str, Any]:
    """The structured difference of two versions (``<name>@<version>``): JSON Pointer paths."""

    def run() -> dict[str, Any]:
        with get_sync_db() as session:
            return service.difference_of(session, a, b)

    return await run_in_threadpool(run)


@rule_sets_router.get("")
async def list_rule_sets(
    page: Page = Depends(paging),
    behaviour_key: str | None = Query(None, pattern=BEHAVIOUR_KEY_PATTERN),
    include_archived: bool = False,
) -> dict[str, Any]:
    """One row per name with its latest version, by name, paged; archived hidden by default."""

    def run() -> dict[str, Any]:
        with get_sync_db() as session:
            return service.list_rule_sets(
                session,
                page=page.page,
                limit=page.limit,
                behaviour_key=behaviour_key,
                include_archived=include_archived,
            )

    return await run_in_threadpool(run)


@rule_sets_router.get("/{name}")
async def get_rule_set(
    name: str = NamePath, version: int | None = Query(None, ge=1)
) -> dict[str, Any]:
    """One version (default the latest): document, sha256, cases, last validation,
    superseded_by and every build that used it."""

    def run() -> dict[str, Any]:
        with get_sync_db() as session:
            return service.get(session, name, version)

    return await run_in_threadpool(run)


@rule_sets_router.post("/{name}/clone", status_code=201)
async def clone_rule_set(
    body: RuleSetCloneIn,
    name: str = NamePath,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> dict[str, Any]:
    """Copy a version to another name and behaviour key with substitutions; it must pass its
    cases before it is saved (FR-016.21)."""
    who = await resolve_who(actor, db)

    def run() -> dict[str, Any]:
        with get_sync_db() as session:
            return service.clone(
                session,
                who,
                name=name,
                version=body.version,
                new_name=body.new_name,
                new_behaviour_key=body.new_behaviour_key,
                substitutions=[s.model_dump() for s in body.substitutions],
                replacement_cases=body.replacement_cases,
            )

    return await run_in_threadpool(run)


async def _set_archived(
    db: AsyncSession, actor: Actor, name: str, body: ArchiveRequest | None, archived: bool
) -> dict[str, Any]:
    who = await resolve_who(actor, db)
    row = await archive_service.set_archived(
        db, "rule_set", name, who, archived=archived, reason=body.reason if body else None
    )
    return {
        "name": row.name,
        "behaviour_key": row.behaviour_key,
        "archived": row.archived_at is not None,
        "archived_at": row.archived_at.isoformat() if row.archived_at else None,
        "next": next_for(
            "dataworks_list_rule_sets",
            {"include_archived": True},
            "list rule sets, archived ones included",
        ),
    }


@rule_sets_router.post("/{name}/archive")
async def archive_rule_set(
    name: str = NamePath,
    body: ArchiveRequest | None = None,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> dict[str, Any]:
    """Hide the rule set (every version) from lists. Nothing is deleted; builds keep reading it."""
    return await _set_archived(db, actor, name, body, True)


@rule_sets_router.post("/{name}/unarchive")
async def unarchive_rule_set(
    name: str = NamePath,
    body: ArchiveRequest | None = None,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> dict[str, Any]:
    return await _set_archived(db, actor, name, body, False)
