""" "Mined from evaluation data" (009 FR-009.59; FTASKS 14.2).

The hard-negative miner (``operators/native/detector/hard_negative_miner.py``) cannot see detector
sets, so the mark is computed here from the recorded lineage: a version built by a recipe whose
``hard_negative_miner`` step read a probe-verdict run (``probe_label_run_id``) over a version that
is bound to an EVALUATION role (in-distribution test or out-of-distribution) of any detector set is
"mined from evaluation data". Check D-4 then treats it as overlapping that role: rows chosen by
looking at a probe's errors on an evaluation set must never train or calibrate against it.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models.detector_set import DetectorSet, DetectorSetRole
from ...models.label_run import LabelRun
from ...models.recipe import RecipeBody
from ...models.version import Version

MINER = "hard_negative_miner"
EVALUATION_ROLES: tuple[str, ...] = ("id_test", "ood_eval")


@dataclass(frozen=True)
class MinedFrom:
    set_id: str
    set_name: str
    role_id: str
    role: str
    source_version_id: str
    probe_label_run_id: str


def mined_from_evaluation(session: Session, version_id: str) -> list[MinedFrom]:
    """Every evaluation role ``version_id`` was mined from, through its own recipe."""
    version = session.get(Version, version_id)
    if version is None:
        return []
    recipe = session.get(RecipeBody, version.recipe_hash)
    steps = (recipe.body.get("steps") if recipe is not None else None) or []
    out: list[MinedFrom] = []
    for step in steps:
        if step.get("operator") != MINER:
            continue
        run_id = (step.get("params") or {}).get("probe_label_run_id")
        run = session.get(LabelRun, str(run_id)) if run_id else None
        if run is None:
            continue
        found = session.execute(
            select(DetectorSet.id, DetectorSet.name, DetectorSetRole.id, DetectorSetRole.role)
            .join(DetectorSetRole, DetectorSetRole.set_id == DetectorSet.id)
            .where(
                DetectorSetRole.version_id == run.input_version_id,
                DetectorSetRole.role.in_(EVALUATION_ROLES),
            )
        ).all()
        for set_id, name, role_id, role in found:
            out.append(
                MinedFrom(set_id, name, role_id, role, str(run.input_version_id), str(run.id))
            )
    return out
