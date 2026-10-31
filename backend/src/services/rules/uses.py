"""Use records: what each step execution ran (FR-016.24; FTID 016 section 3.7, I3).

Called by ``step_ingest.ingest`` BEFORE its single commit, so a ``rule_set@1`` step is never
completed without its ``dw_rule_set_uses`` row. The record comes from the PIN (the step's own
parameters, authoritative: an empty input runs no batch and writes no report), cross-checked against
every batch report's ``rule_set_use`` (deduplicated): a report that disagrees with the pin, or a pin
no stored version answers, fails the step ``rule_set_use_unrecorded``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ...models.publish import ConfigKind, ConfigVersion
from ...models.rule_set import RuleSetUse
from ...models.step_execution import StepExecution
from .errors import RuleSetError
from .step_resolution import OPERATOR, pin_of


def _unrecorded(message: str, details: dict[str, Any]) -> RuleSetError:
    return RuleSetError(422, "rule_set_use_unrecorded", message, details)


def expected_use(session: Session, params: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """``(config_version_id, the use payload)`` the pin names, or ``rule_set_use_unrecorded``."""
    pin = pin_of(params)
    if pin is None:
        raise _unrecorded("The step's rule-set pin is incomplete; no use can be recorded.", {})
    name, version, sha = pin
    row = session.execute(
        select(ConfigVersion).where(
            ConfigVersion.kind == ConfigKind.RULE_SET.value,
            ConfigVersion.name == name,
            ConfigVersion.number == version,
            ConfigVersion.body_sha256 == sha,
        )
    ).scalar_one_or_none()
    if row is None:
        raise _unrecorded(
            f"No stored rule set {name}@{version} with sha256 {sha}: the use cannot be recorded.",
            {"name": name, "version": version, "sha256": sha},
        )
    return row.id, {
        "name": name,
        "version": version,
        "sha256": sha,
        "behaviour_key": str(row.body["behaviour_key"]),
        "use": str(params.get("use")),
    }


def record_for_step(
    session: Session,
    execution: StepExecution,
    step_params: Mapping[str, Any],
    reports: Sequence[Mapping[str, Any]] | None,
) -> dict[str, Any] | None:
    """Insert the step's use row (in the caller's transaction); return its payload. A step of any
    other operator records nothing (None)."""
    if execution.operator_name != OPERATOR:
        return None
    config_version_id, payload = expected_use(session, step_params)
    reported = {
        tuple(sorted(r["rule_set_use"].items()))
        for r in reports or []
        if isinstance(r, Mapping) and isinstance(r.get("rule_set_use"), Mapping)
    }
    for item in reported:
        if dict(item) != payload:
            raise _unrecorded(
                "The step reported running a rule set other than the one its pin names.",
                {"pinned": payload, "reported": dict(item)},
            )
    session.execute(
        pg_insert(RuleSetUse)
        .values(
            step_execution_id=execution.id,
            config_version_id=config_version_id,
            rule_set_sha256=payload["sha256"],
            behaviour_key=payload["behaviour_key"],
            use=payload["use"],
        )
        .on_conflict_do_nothing(index_elements=["step_execution_id", "config_version_id"])
    )
    return payload
