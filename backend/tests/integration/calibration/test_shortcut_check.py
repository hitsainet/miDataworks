"""The calibration shortcut check reads 004's held-out figure through the real record path
(FR-006.14, FR-004.29; suite tracker 2026-10-17).

Until 2026-10-17 ``record_service`` passed ``shortcut=None`` unconditionally, so every record's
``shortcut_comparison`` said "004's held-out predictiveness (FR-004.29) is not served", although
004 had served it since 2026-10-07; ``test_compute_job`` even asserted that ``not_applicable``.
These tests run the real job body on a version whose rows have enough of both classes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import numpy as np

from tests.integration.calibration.test_compute_job import make_set, start
from tests.support.calibration_fixtures import MAPPING, Runner, humor_scores, make_run, make_version

#: No originals and no reference: every row is a labeled edit, half funny, half not.
SET_MAPPING = {k: v for k, v in MAPPING.items() if k not in ("reference", "strata")}


def rows(n: int = 200, *, leak: bool) -> list[dict[str, Any]]:
    rng = np.random.default_rng(7)
    out: list[dict[str, Any]] = []
    for i in range(n):
        funny = i % 2 == 0
        grades = [2, 2, 2, 2, 2] if funny else [0, 0, 0, 0, 1]
        row = {
            "text": f"edit {i}",
            "meanGrade": round(float(np.mean(grades)), 1),
            "grades": "".join(str(g) for g in grades),
            "pair_id": f"p{i // 2:04d}",
            "kind": "edited",
            "desk": str(rng.choice(["a", "b", "c"])),
        }
        if leak:
            row["source_feed"] = "satire" if funny else "wire"
        out.append(row)
    return out


async def record_for(
    client: httpx.AsyncClient, runner: Runner, data_dir: Path, *, leak: bool
) -> dict[str, Any]:
    data = rows(leak=leak)
    version = make_version(data_dir, data)
    set_id = await make_set(client, version, SET_MAPPING)
    run = make_run(version, humor_scores(data))
    started = await start(client, run.id, set_id)
    assert started.status_code == 202, started.text
    result = runner.run(started.json()["job_id"])
    assert result["status"] == "completed", result
    return dict((await client.get(f"/api/v1/calibration-records/{result['record_id']}")).json())


def shortcut_check(record: dict[str, Any]) -> dict[str, Any]:
    (check,) = [
        c
        for c in record["checks"]
        if c["metric_id"] == "auroc" and c["check_id"] == "shortcut_comparison"
    ]
    return dict(check)


async def test_a_leaking_metadata_column_fails_the_check_by_name(
    client: httpx.AsyncClient, operator_name: str, runner: Runner, data_dir: Path
) -> None:
    record = await record_for(client, runner, data_dir, leak=True)
    check = shortcut_check(record)
    assert check["result"] == "fail", check
    assert check["reason"] == "column 'source_feed' alone reaches 1.000"
    assert check["statistic"]["columns"]["source_feed"] == 1.0
    assert check["statistic"]["labeler_auroc"] == record["metrics"]["auroc"]["value"]


async def test_unrelated_columns_pass_with_004s_figures_and_label_sources_are_excluded(
    client: httpx.AsyncClient, operator_name: str, runner: Runner, data_dir: Path
) -> None:
    record = await record_for(client, runner, data_dir, leak=False)
    check = shortcut_check(record)
    assert check["result"] == "pass", check
    figures = check["statistic"]["columns"]
    # 004's figure for each audited metadata column; the label-defining ones are not there
    assert set(figures) == {"desk", "kind", "pair_id"}
    assert all(v < check["statistic"]["labeler_auroc"] for v in figures.values())
    short = record["metrics"]["shortcut"]
    assert short["figures"] == figures
    excluded = {e["column"]: e["reason"] for e in short["excluded"]}
    assert excluded["meanGrade"] == "label_source" and excluded["grades"] == "label_source"
    assert short["n_rows"] == record["metrics"]["auroc"]["n"]
    assert "not served" not in (check["reason"] or "")
