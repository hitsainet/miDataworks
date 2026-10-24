"""Build-time resolution of a ``rule_set@1`` step (FR-016.13, FR-016.22, FR-016.23; FTID 016 section 3.6).

A recipe step PINS ``rule_set_name``, ``rule_set_version`` and ``rule_set_sha256``.
:func:`resolve_params` (pure) turns the pin into the parameters the step runs with:

- refuses a recipe that supplies ``rule_set_document`` itself (``rule_set_document_reserved``);
- refuses a pin no stored version answers (``rule_set_not_found``) or whose stored sha256 differs
  (``rule_set_hash_mismatch``); the version is EXACTLY the pinned one, never "the latest";
- re-runs the stored document's embedded cases (FR-016.13): a validator that changed under a stored
  rule set is caught here, naming the case (``case_failed``);
- returns a copy with ``rule_set_document`` = the stored canonical text.

Caps never apply here (FTDD 016 section 8.3): a later reduction cannot break a rebuild.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ...models.publish import ConfigKind, ConfigVersion
from ...rules import cases, rule_set
from ...rules.compose import RuleSetNotRunnable
from ..step_params import Lookup, register_step_param_resolver
from .errors import RuleSetError

OPERATOR = "rule_set"
DOCUMENT_PARAM = "rule_set_document"


def pin_of(params: Mapping[str, Any]) -> tuple[str, int, str] | None:
    """``(name, version, sha256)`` of a well-formed pin, else None (params validation reports it)."""
    name, version, sha = (
        params.get("rule_set_name"),
        params.get("rule_set_version"),
        params.get("rule_set_sha256"),
    )
    if not isinstance(name, str) or isinstance(version, bool) or not isinstance(version, int):
        return None
    if not isinstance(sha, str):
        return None
    return name, version, sha


@functools.lru_cache(maxsize=256)
def _recheck(body_canonical: str) -> None:
    """Run the stored document's own cases; raise naming the first failing case. Cached per text
    (a passing check is pure: the same bytes and the same process's validators)."""
    document = rule_set.parse(json.loads(body_canonical))
    try:
        outcomes = cases.run_document_cases(document)
    except RuleSetNotRunnable as exc:
        raise RuleSetError(
            409,
            exc.code,
            f"The stored rule set {document.name} can no longer run here: {exc}",
            {"rule_set": document.name, **exc.details},
        ) from None
    for case_id, outcome in outcomes:
        if not outcome.passed:
            raise RuleSetError(
                409,
                "case_failed",
                f"The stored rule set {document.name} fails its own case {case_id!r} "
                f"({'; '.join(outcome.mismatches)}). A validator changed under it; save a new "
                "version whose cases pass, then move the recipe to it.",
                {"rule_set": document.name, "case": case_id, "mismatches": outcome.mismatches},
                next_tool="dataworks_validate_rule_set",
                next_args={"name": document.name},
                why="see every failing case of the stored version",
            )


def resolve_params(params: Mapping[str, Any], lookup: Lookup) -> dict[str, Any]:
    """The pinned parameters with ``rule_set_document`` filled in (see the module docstring)."""
    if DOCUMENT_PARAM in params:
        raise RuleSetError(
            422,
            "rule_set_document_reserved",
            "rule_set_document is filled in by the build from the pinned version; a recipe "
            "names only rule_set_name, rule_set_version and rule_set_sha256.",
            {"param": DOCUMENT_PARAM},
        )
    pin = pin_of(params)
    if pin is None:
        raise RuleSetError(
            422,
            "rule_set_not_resolved",
            "The step's rule-set pin is incomplete: it needs rule_set_name, rule_set_version "
            "(an integer) and rule_set_sha256.",
            {},
            next_tool="dataworks_list_rule_sets",
            why="find the rule set to pin",
        )
    name, version, pinned = pin
    found = lookup(name, version)
    if found is None:
        raise RuleSetError(
            404,
            "rule_set_not_found",
            f"No stored rule set {name} version {version}.",
            {"name": name, "version": version},
            next_tool="dataworks_list_rule_sets",
            why="find a stored rule set to pin",
        )
    stored, body = found
    if stored != pinned:
        raise RuleSetError(
            409,
            "rule_set_hash_mismatch",
            f"The recipe pins {name}@{version} with sha256 {pinned}, but the stored version's is "
            f"{stored}. Re-pin from dataworks_get_rule_set.",
            {"name": name, "version": version, "pinned": pinned, "stored": stored},
            next_tool="dataworks_get_rule_set",
            next_args={"name": name, "version": version},
            why="read the stored version's sha256",
        )
    _recheck(body)
    return {**params, DOCUMENT_PARAM: body}


def _pins(steps: Sequence[Mapping[str, Any]]) -> set[tuple[str, int]]:
    out: set[tuple[str, int]] = set()
    for step in steps:
        if step.get("operator") != OPERATOR:
            continue
        pin = pin_of(step.get("params") or {})
        if pin is not None:
            out.add((pin[0], pin[1]))
    return out


def _query(pins: set[tuple[str, int]]) -> Any:
    return select(
        ConfigVersion.name,
        ConfigVersion.number,
        ConfigVersion.body_sha256,
        ConfigVersion.body_canonical,
    ).where(
        ConfigVersion.kind == ConfigKind.RULE_SET.value,
        tuple_(ConfigVersion.name, ConfigVersion.number).in_(sorted(pins)),
    )


async def preload(
    db: AsyncSession, steps: Sequence[Mapping[str, Any]]
) -> dict[tuple[str, int], tuple[str, str]]:
    """Every stored version the steps pin, in one query (absent pins are simply not in the map)."""
    pins = _pins(steps)
    if not pins:
        return {}
    rows = (await db.execute(_query(pins))).all()
    return {(r[0], int(r[1])): (str(r[2]), str(r[3])) for r in rows}


def sync_lookup(session: Session) -> Lookup:
    def lookup(name: str, version: int) -> tuple[str, str] | None:
        row = session.execute(_query({(name, version)})).first()
        return None if row is None else (str(row[2]), str(row[3]))

    return lookup


register_step_param_resolver(OPERATOR, resolve_params)
