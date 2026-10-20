"""The third place judge independence is enforced: 005's label-run preflight (FR-007.25; T-35).

Registered into ``label_run_preflight.PREFLIGHT_CHECKS`` (005 FR-005.54) through
``label_run_preflight_registrations.REGISTRATION_MODULES``. When a label run's input version holds
generated rows — any ``generation_run`` binding on the version or on an ancestor it was built
from — every bound run's generator identities are loaded and compared with the labeler's
identity by :func:`rules.judge_conflicts`. Judges AND classifiers are refused (T-35): a classifier
that is the generator would grade its own output just the same.

A check runs synchronously inside 005's plan; it opens its own short sync session.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models.generation import GenerationRun
from ...models.version import Version, VersionInput
from ..label_run_preflight import PreflightContext, PreflightRefused, register
from . import rules

#: Lineage walk bound: a version chain deeper than this is not plausible.
MAX_ANCESTORS = 256


def bound_generation_runs(session: Session, version_id: str) -> list[str]:
    """Every generation run bound by the version or by any version it was built from."""
    seen: set[str] = set()
    runs: list[str] = []
    frontier = [str(version_id)]
    while frontier and len(seen) < MAX_ANCESTORS:
        current = frontier.pop()
        if current in seen:
            continue
        seen.add(current)
        version = session.get(Version, current)
        if version is None:
            continue
        for binding in version.bindings or []:
            if binding.get("kind") == "generation_run" and binding["id"] not in runs:
                runs.append(str(binding["id"]))
        parents = session.execute(
            select(VersionInput.input_version_id).where(
                VersionInput.version_id == current, VersionInput.input_version_id.is_not(None)
            )
        ).scalars()
        frontier.extend(str(p) for p in parents)
    return runs


def generator_identities(session: Session, run_ids: list[str]) -> list[rules.Identity]:
    out: list[rules.Identity] = []
    for run_id in run_ids:
        run = session.get(GenerationRun, run_id)
        if run is None:
            continue
        out.extend(rules.identity_from(i) for i in run.generator_identities)
    return out


def check_label_run(context: PreflightContext, session: Session) -> None:
    run_ids = bound_generation_runs(session, context.input_version_id)
    if not run_ids:
        return
    labeler = rules.generator_identity(context.model_id, context.model_revision, None)
    conflicts = rules.judge_conflicts(labeler, generator_identities(session, run_ids))
    if conflicts:
        error = rules.conflict_error(labeler, conflicts, inherited_from=None)
        details: dict[str, Any] = {
            **error.details,
            "generation_runs": run_ids,
            "role": context.role,
        }
        raise PreflightRefused(
            "JUDGE_IS_GENERATOR",
            f"This {context.role} run would label rows its own model generated. " + error.message,
            details,
        )


def label_run_preflight(context: PreflightContext) -> None:
    """The registered check (005's registry calls it with the plan's context)."""
    from ...core.database import get_sync_db

    with get_sync_db() as session:
        check_label_run(context, session)


register(label_run_preflight)
