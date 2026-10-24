"""The leakage repair D-4 names (PII run F-4, 2026-10-20).

D-4 refused the run's first set over 189 near-duplicate pairs and pointed to "feature 004's leakage
view", which no MCP tool reaches. ``plan`` reads the SAME leakage report D-4 reads (the identical
``check_leakage`` call over the set's completed roles, so it is found stored, not recomputed) and,
for every crossing pair, keeps the side that matters more for an honest evaluation and drops the
other:

    ood_eval  >  id_test  >  calibration_negatives  >  train   (kept first)

Two roles of one kind (two out-of-distribution evaluations) keep the earlier-bound one. For each
role that gives rows up, the plan returns the rows it would lose and a one-step recipe of
``drop_leakage_pairs`` pinned to the report's pairs file by SHA-256. Building it (the ordinary
``dataworks_save_recipe`` + ``dataworks_build_version``, under the ordinary build gate) gives a
new version to bind to that role. The plan writes nothing.
"""

from __future__ import annotations

from typing import Any

import pyarrow.parquet as pq
from sqlalchemy.orm import Session

from ...core.storage import resolve_under_data_dir
from ...models.curation import VersionReport
from ...models.enums import VersionState
from ...models.version import Version
from ..curation import api as curation
from . import set_service

#: Lower drops first; an evaluation role is kept whole whenever the other side can give way.
KEEP_RANK = {"train": 0, "calibration_negatives": 1, "id_test": 2, "ood_eval": 3}
OPERATOR = "drop_leakage_pairs"


def plan(session: Session, set_id: str) -> dict[str, Any]:
    from ...operators.native.curation.drop_listed_rows import crossing_keys

    set_row = set_service.get_set(session, set_id)
    roles = set_service.roles_of(session, set_id)
    completed = [
        r
        for r in roles
        if (v := session.get(Version, r.version_id)) is not None
        and v.state == VersionState.COMPLETED
    ]
    if len(completed) < 2:
        return {
            "set_id": set_id,
            "crossing_pairs": 0,
            "repairs": [],
            "message": "Fewer than two completed roles: nothing can cross.",
        }
    paired = any(r.pair_column for r in completed)
    group_columns = {r.id: (r.pair_column or None) for r in completed} if paired else None
    result = curation.check_leakage(
        [(r.version_id, r.split, r.id) for r in completed],
        group_columns=group_columns,
        session=session,
    )
    by_id = {r.id: r for r in completed}
    order = {r.id: i for i, r in enumerate(completed)}
    if not result.pairs_artefact:
        return {
            "set_id": set_id,
            "crossing_pairs": 0,
            "repairs": [],
            "message": "No pair crosses two roles: D-4 is green.",
        }
    report = session.get(VersionReport, result.report_id) if result.report_id else None
    artefact = next(
        (
            a
            for a in (report.artefacts if report is not None else []) or []
            if a.get("path") == result.pairs_artefact
        ),
        None,
    )
    pairs = [
        p
        for p in pq.read_table(resolve_under_data_dir(result.pairs_artefact)).to_pylist()
        if p["side_a"] != p["side_b"] and p["side_a"] in by_id and p["side_b"] in by_id
    ]

    def rank(role_id: str) -> tuple[int, int]:
        return (KEEP_RANK.get(by_id[role_id].role, 0), -order[role_id])

    loses: dict[str, set[str]] = {}
    for p in pairs:
        a, b = str(p["side_a"]), str(p["side_b"])
        drop, keep = (a, b) if rank(a) < rank(b) else (b, a)
        loses.setdefault(drop, set()).add(keep)
    repairs = []
    for role_id in sorted(loses, key=lambda r: order[r]):
        role = by_id[role_id]
        keep_sides = sorted(loses[role_id])
        rows = crossing_keys(pairs, role_id, set(keep_sides))
        repairs.append(
            {
                "role_id": role_id,
                "role": role.role,
                "role_name": set_service.role_name(set_row, role, roles),
                "version_id": role.version_id,
                "split": role.split,
                "rows_to_drop": len(rows),
                "keeps_whole": [{"role_id": k, "role": by_id[k].role} for k in keep_sides],
                "recipe_body": {
                    "format": "dw.recipe/v1",
                    "steps": [
                        {
                            "operator": OPERATOR,
                            "version": "1.0.0",
                            "params": {
                                "pairs_artefact": result.pairs_artefact,
                                "pairs_sha256": artefact["sha256"] if artefact else None,
                                "drop_side": role_id,
                                "keep_sides": keep_sides,
                            },
                        }
                    ],
                },
            }
        )
    return {
        "set_id": set_id,
        "crossing_pairs": len(pairs),
        "leakage_report_id": result.report_id,
        "rule": "Each crossing pair keeps ood_eval over id_test over calibration_negatives over "
        "train; two roles of one kind keep the earlier-bound one.",
        "repairs": repairs,
        "next_steps": [
            "dataworks_save_recipe(name, recipe_body) for each repair",
            "dataworks_build_version(the role's dataset, the recipe's revision, "
            "inputs=[{kind: version, version_id}]) (the ordinary build gate applies)",
            "dataworks_update_detector_set to bind the new version to the role",
            "dataworks_check_detector_set: D-4 should read green",
        ],
    }
