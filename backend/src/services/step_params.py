"""Build-time step parameters filled from stored records (feature 016; FTID 016 section 3.6, I1).

A recipe step stores a PIN; some operators need the pinned record's content to run. A resolver,
registered per operator name, turns the pinned parameters into the parameters the step runs with
(``rule_set@1``: ``rule_set_document`` = the stored canonical text). A step whose operator has no
resolver passes through unchanged, so no existing step's identity moves.

**017 ``"@input"`` (FTID 017 §7.10, TD-017.1).** A behaviour step after the plan names a parameter
``"@input"`` to read what the build's input version RECORDED (its ``behaviour_definition`` and
``behaviour_configuration``): :func:`resolve_inputs` replaces each registered parameter whose value
is exactly ``"@input"`` with that record as canonical JSON text, before the rule-set resolver runs,
so the step's identity digests the literal configuration (a changed input changes the step) and the
operator sees literal parameters. Recipe validation has no input version (a recipe is not tied to
one), so it leaves ``"@input"`` in place and the consuming operator accepts it as deferred; the
build orchestrator resolves it, and refuses ``input_not_recorded`` when the input recorded nothing.
The consumers register with :func:`register_input_params` (017 slices 3 and 5).

The resolver is pure: ``resolver(params, lookup)``. Each caller supplies the lookup the way its
context allows (FTID I1): recipe validation is synchronous with no session, so its async callers
preload the records (:func:`preload`) and pass a mapping; the build orchestrator runs on a sync
session and passes ``sync_lookup(session)``. Callers: ``recipe_service.validate_body`` (via six
async call sites), ``build_orchestrator._advance`` (identity and dispatch), the operator preview and
the upgrade plan (``api/v1/endpoints/operators.py``).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

#: ``(name, version) -> (sha256, canonical text)`` of a stored record, or None.
Lookup = Callable[[str, int], tuple[str, str] | None]
Resolver = Callable[[Mapping[str, Any], Lookup], dict[str, Any]]

#: operator name -> resolver. Populated at import by the owning feature (016: ``rule_set``).
STEP_PARAM_RESOLVERS: dict[str, Resolver] = {}


def register_step_param_resolver(operator: str, resolver: Resolver) -> None:
    STEP_PARAM_RESOLVERS[operator] = resolver


#: 017: the value that reads the build input's recorded behaviour configuration.
INPUT_REFERENCE = "@input"
#: operator name -> the parameters that may say ``"@input"`` (the owning feature registers them).
INPUT_PARAMS: dict[str, frozenset[str]] = {}
#: A parameter named here reads that record whole; any other reads the configuration's field.
INPUT_RECORDS = {
    "definition": "behaviour_definition",
    "configuration": "behaviour_configuration",
}


def register_input_params(operator: str, names: Sequence[str]) -> None:
    INPUT_PARAMS[operator] = frozenset(names)


def input_records(session: Session, inputs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """What the build's input version recorded (its first ``version`` input), or empty."""
    from ..models.version import Version

    version_id = next((i.get("version_id") for i in inputs if i.get("kind") == "version"), None)
    row = session.get(Version, version_id) if version_id else None
    if row is None:
        return {}
    return {
        "version_id": str(row.id),
        "behaviour_definition": row.behaviour_definition,
        "behaviour_configuration": row.behaviour_configuration,
    }


def resolve_inputs(step: Mapping[str, Any], records: Mapping[str, Any]) -> dict[str, Any]:
    """The step with each registered ``"@input"`` parameter replaced by the input's record (a
    copy). Refuses ``input_not_recorded`` naming the parameter when the input recorded nothing."""
    from ..core.errors import AppError

    names = INPUT_PARAMS.get(str(step.get("operator")), frozenset())
    params = dict(step.get("params") or {})
    for name in sorted(names):
        if params.get(name) != INPUT_REFERENCE:
            continue
        record = records.get(INPUT_RECORDS.get(name, "behaviour_configuration"))
        value = record if name in INPUT_RECORDS else (record or {}).get(name)
        if value is None:
            raise AppError(
                f'Step parameter {name!r} reads "@input", but the build\'s input version '
                f"{records.get('version_id') or '(none: a source input)'} recorded no "
                f"{name}. Build from the carrier-plan version (or its descendant), or give the "
                "value itself.",
                code="input_not_recorded",
                status_code=409,
                details={
                    "param": name,
                    "version_id": records.get("version_id"),
                    "next": {
                        "tool": "dataworks_get_version",
                        "args": {"version_id": records.get("version_id")},
                        "why": "see what the input version recorded",
                    },
                },
            )
        params[name] = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return {**step, "params": params}


def needs_resolution(step: Mapping[str, Any]) -> bool:
    return str(step.get("operator")) in STEP_PARAM_RESOLVERS


def resolve_step(step: Mapping[str, Any], lookup: Lookup) -> dict[str, Any]:
    """The step with its parameters resolved (a copy); unchanged when no resolver is registered.
    Raises the resolver's ``AppError`` (``rule_set_not_found``, ``rule_set_hash_mismatch``, ...)."""
    resolver = STEP_PARAM_RESOLVERS.get(str(step.get("operator")))
    if resolver is None:
        return dict(step)
    return {**step, "params": resolver(dict(step.get("params") or {}), lookup)}


async def preload(
    db: AsyncSession, steps: Sequence[Mapping[str, Any]]
) -> dict[tuple[str, int], tuple[str, str]]:
    """Every record the steps pin, read once, for :func:`resolve_step` without a session."""
    from .rules import step_resolution

    return await step_resolution.preload(db, steps)


def sync_lookup(session: Session) -> Lookup:
    from .rules import step_resolution

    return step_resolution.sync_lookup(session)


def mapping_lookup(found: Mapping[tuple[str, int], tuple[str, str]]) -> Lookup:
    return lambda name, version: found.get((name, version))


# The owning features' resolvers register at import (the reachability test reads the registry).
from .rules import step_resolution as _rule_sets  # noqa: E402,F401
