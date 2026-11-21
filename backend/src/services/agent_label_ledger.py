"""The P-07 window: agent labelling rows per data scope per 24 h (010 FTDD sections 4.3, 5.4).

Feature 010 owns this module; it lands with feature 002 because 002's build routes carry the
``agent_label_rows`` gate (FR-002.50; S3-02). The rules are 010's:

- ``needs_approval = window_total + rows_for_this_run > AGENT_LABEL_ROW_THRESHOLD`` (strict ``>``);
- identity is NOT in the filter (two agent identities must not split one budget);
- the check and the ledger write run under one PostgreSQL advisory lock keyed on the scope;
- operator-origin runs write nothing; the decorator never reads a count from the request body.

**Builds (FR-002.50).** A step counts when its operator's manifest declares an ``endpoint_role`` or
is one of feature 009's probe-verdict or feature-tagging labelers. Its rows are its input rows. Before
a build runs, a step's input rows are not known; the count is the build's total input rows per
labelling step — an upper bound, so the gate fails CLOSED. Cached-label reuse is subtracted by
feature 005's counter once it exists; until then nothing is subtracted.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.clock import utc_now
from ..core.config import get_settings
from ..models.agent_label_row import AgentLabelRow

#: The default window length (010 FTDD section 5.4: 24 h, matching the approval lifetime). The
#: deployment sets it with ``AGENT_LABEL_WINDOW_HOURS`` (010 FTID section 9).
AGENT_LABEL_WINDOW_HOURS = 24


def _lock_key(scope: str) -> int:
    return int(uuid.UUID(scope).int % (2**62))


async def lock_scope(db: AsyncSession, scope: str) -> None:
    await db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _lock_key(scope)})


async def window_total(db: AsyncSession, scope: str) -> int:
    since = utc_now() - timedelta(hours=get_settings().agent_label_window_hours)
    total = (
        await db.execute(
            select(func.coalesce(func.sum(AgentLabelRow.rows_counted), 0)).where(
                AgentLabelRow.version_id == scope, AgentLabelRow.admitted_at > since
            )
        )
    ).scalar_one()
    return int(total)


async def needs_approval(db: AsyncSession, scope: str, rows_for_run: int) -> bool:
    await lock_scope(db, scope)
    threshold = get_settings().agent_label_row_threshold
    return await window_total(db, scope) + rows_for_run > threshold


async def admit(
    db: AsyncSession, scope: str, identity: str, run_kind: str, run_id: str, rows_counted: int
) -> AgentLabelRow:
    """Record an admitted agent run (started without approval, or executed after approval)."""
    await lock_scope(db, scope)
    row = AgentLabelRow(
        id=str(uuid.uuid4()),
        version_id=scope,
        identity=identity,
        run_kind=run_kind,
        run_id=run_id[:64],
        rows_counted=rows_counted,
    )
    db.add(row)
    await db.flush()
    return row


async def build_needs_approval(values: dict[str, Any], db: AsyncSession) -> bool:
    """The ``when=`` predicate on ``POST /versions`` and ``POST /recipes/{id}/build``.

    A request naming a revision, recipe, dataset or input that does not exist is refused here
    with the route's own ``404`` (ENH-006 G22: an agent's typo used to become an operator's
    approval, failing ``revision_not_found`` only after the click). After that it fails closed:
    anything it cannot resolve raises, and the decorator then gates the call.
    """
    from ..core.agent_origin import RefusedBeforeGate
    from ..core.errors import AppError
    from .version_build_service import labelling_plan_for_route, require_build_references

    try:
        await require_build_references(db, values)
    except AppError as missing:
        raise RefusedBeforeGate(missing) from missing
    plan = await labelling_plan_for_route(db, values)
    if not plan.steps:
        return False
    return await needs_approval(db, plan.scope, plan.rows_total)


# --- 021 (suite P42): the version build routes also gate an agent's lowering of sensitivity ---


async def lowers_sensitivity(values: dict[str, Any], db: AsyncSession) -> bool:
    """Whether the gated build declares data ``not_sensitive`` over inputs not already so. Any
    lookup error RAISES, so the decorator gates the call (fails closed, R1A-D18)."""
    from .version_build_service import sensitivity_facts_for_route

    facts = await sensitivity_facts_for_route(db, values)
    return bool(facts["lowered"])


async def build_gate_when(values: dict[str, Any], db: AsyncSession) -> bool:
    """The ``when=`` of both version build routes: the label-row window (P-07) OR a lowering of
    sensitivity (P42). ``build_needs_approval`` runs first: a request naming something that does
    not exist is refused with its 404 before either gate (ENH-006 G22)."""
    return await build_needs_approval(values, db) or await lowers_sensitivity(values, db)


async def sensitivity_enrich(values: dict[str, Any], db: AsyncSession) -> dict[str, Any]:
    """The approval card's facts and what a ``sensitivity_declare`` approval binds to: the
    inputs the build would lower and the digest ``request_build`` checks on replay. A lookup that
    fails records itself, and the call waits for the operator as ``sensitivity_declare``."""
    from .version_build_service import sensitivity_facts_for_route

    try:
        return await sensitivity_facts_for_route(db, values)
    except Exception:  # noqa: BLE001 - fail closed: the operator decides
        return {"lowered": None, "sensitivity_digest": None, "sensitivity_unresolved": True}


async def recipe_build_enrich(values: dict[str, Any], db: AsyncSession) -> dict[str, Any]:
    """``sensitivity_enrich`` plus the recipe head revision the approval card was built from
    (R2 review fix (OR2-021-1), 2026-11-16). ``POST /recipes/{id}/build`` builds the HEAD, and the
    revision route is ungated: without this an agent could queue a small build, revise the head,
    and have the operator's approval run a different recipe. ``expected_head_revision_id`` names
    the route's hidden parameter, so the replay receives it and refuses ``recipe_head_moved`` when
    the head has moved. Always set (``None`` when the recipe cannot be read), so a value the agent
    passed itself never reaches the stored request; ``None`` never matches a head, so it fails
    CLOSED."""
    from . import recipe_service

    facts = await sensitivity_enrich(values, db)
    try:
        recipe = await recipe_service.get_recipe_row(db, values["recipe_id"])
        head = str(recipe.head_revision_id) if recipe.head_revision_id else None
    except Exception:  # noqa: BLE001 - fail closed: no head recorded matches no head
        head = None
    return {**facts, "expected_head_revision_id": head}


def build_action(values: dict[str, Any], payload: dict[str, Any]) -> str:
    """``sensitivity_declare`` when the build lowers sensitivity (or that could not be read),
    else ``agent_label_rows``."""
    if payload.get("lowered") or payload.get("sensitivity_unresolved"):
        return "sensitivity_declare"
    return "agent_label_rows"
