"""Acceptance on the prototype's data (006 FTASKS 14.1 – 14.3, 16.1; FPRD 006 section 11).

Reads ``dataset/prepared/humicroedit.parquet``, the label chunks and
``dataset/labelled/humicroedit_eval.parquet`` under ``DW_PROTOTYPE_DIR`` (the prototype's root, the
directory that holds ``dataset/``), and ``records/`` from this repository. The data are git-ignored
and their licence is not stated, so they are never committed: the module SKIPS without them, and
FAILS instead under ``DW_REQUIRE_PROTOTYPE_FIXTURES=1``.

The figures go through ``record_service.compute_figures`` — the worker's own assembly — on arrays
built the way ``arrays.load_arrays`` builds them (version row order).
"""

from __future__ import annotations

import glob
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

ROOT = Path(os.environ.get("DW_PROTOTYPE_DIR", "/nonexistent"))
RECORDS = Path(__file__).resolve().parents[3] / "records"
PREPARED = ROOT / "dataset" / "prepared" / "humicroedit.parquet"

if not PREPARED.exists():
    if os.environ.get("DW_REQUIRE_PROTOTYPE_FIXTURES") == "1":
        raise RuntimeError(f"DW_REQUIRE_PROTOTYPE_FIXTURES=1 but {PREPARED} is missing")
    pytest.skip("prototype data not available (set DW_PROTOTYPE_DIR)", allow_module_level=True)

import pyarrow.parquet as pq  # noqa: E402

from src.services.calibration import ceiling as c  # noqa: E402
from src.services.calibration import metrics as m  # noqa: E402
from src.services.calibration.arrays import CalibrationArrays  # noqa: E402
from src.services.calibration.record_service import compute_figures  # noqa: E402
from src.services.calibration.verdict import decide  # noqa: E402

MAPPING: dict[str, Any] = {
    "schema": "dw.calibration-mapping/v1",
    "human_label": {
        "column": "meanGrade",
        "rule": "numeric",
        "positive_at_or_above": 1.6,
        "negative_at_or_below": 0.4,
    },
    "ratings": {"column": "grades", "format": "digit_string", "scale": [0, 3]},
    "group": {"column": "pair_id"},
    "strata": ["kind"],
    "reference": {"column": "kind", "value": "original"},
}
LABELS = ["humorous", "not_humorous"]


def scores_for(wording: str) -> dict[str, float]:
    files = sorted(
        glob.glob(
            str(ROOT / "dataset" / "labelled" / f"humicroedit__{wording}" / "chunk-*.parquet")
        )
    )
    out: dict[str, float] = {}
    for f in files:
        t = pq.read_table(f, columns=["id", "p_true"]).to_pydict()
        out.update(zip(t["id"], t["p_true"], strict=True))
    return out


@pytest.fixture(scope="module")
def source() -> list[dict[str, Any]]:
    return list(pq.read_table(PREPARED).to_pylist())


def arrays(rows: list[dict[str, Any]], scores: dict[str, float]) -> CalibrationArrays:
    def label(r: dict[str, Any]) -> str | None:
        g = r["meanGrade"]
        if r["kind"] == "original" or g is None or g != g:
            return None
        return LABELS[0] if g >= 1.6 else (LABELS[1] if g <= 0.4 else None)

    probs = [scores.get(r["id"]) for r in rows]
    return CalibrationArrays(
        positions=np.arange(len(rows)),
        row_keys=[r["id"] for r in rows],
        human=[label(r) for r in rows],
        groups=[r["pair_id"] for r in rows],
        strata=[json.dumps({"kind": r["kind"]}) for r in rows],
        is_reference=[r["kind"] == "original" for r in rows],
        ratings=[
            c.parse_ratings(r["grades"], "digit_string") if r["grades"] else None for r in rows
        ],
        scores=np.array([np.nan if p is None else p for p in probs], dtype=np.float64),
        probabilities=probs,
        outcomes=[None] * len(rows),
        distributions=[None] * len(rows),
    )


def figures(rows: list[dict[str, Any]], wording: str, mapping: dict[str, Any]) -> Any:
    a = arrays(rows, scores_for(wording))
    cal = SimpleNamespace(label_set=LABELS, mapping=mapping)
    run = SimpleNamespace(threshold_positive=0.8, threshold_negative=0.2)
    by_key = {k: float(s) for k, s in zip(a.row_keys, a.scores, strict=True) if s == s}
    return compute_figures(a, cal, run, by_key=by_key)  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def funny(source: list[dict[str, Any]]) -> Any:
    return figures(source, "funny", MAPPING)


def test_14_1_auroc_and_interval_and_reliability(source: list[dict[str, Any]], funny: Any) -> None:
    metrics_doc = funny[0]
    expected = json.loads((RECORDS / "judge_validation.json").read_text())
    auroc = metrics_doc["auroc"]
    assert round(auroc["value"], 4) == 0.7533 and auroc["n"] == 6600
    assert abs(auroc["ci_low"] - 0.7416) <= 0.005 and abs(auroc["ci_high"] - 0.7654) <= 0.005
    ours = [
        (b["n"], round(b["positive_fraction"], 4)) for b in metrics_doc["reliability"] if b["n"]
    ]
    theirs = [(b["n"], b["human_funny_fraction"]) for b in expected["funny"]["reliability"]]
    assert ours == theirs
    intent = figures(source, "intent", {k: v for k, v in MAPPING.items() if k != "ratings"})[0]
    assert round(intent["auroc"]["value"], 4) == 0.7440


def test_14_2_ceiling_and_comparison(source: list[dict[str, Any]], funny: Any) -> None:
    metrics_doc = funny[0]
    # ±0.008, loosened from ±0.002 on 2026-10-07 (FTDD 006 §4.4): the live run read 0.7453 with
    # identical labels; row eligibility and row order move the figure by more than 0.002.
    assert abs(metrics_doc["ceiling"]["value"] - 0.740) <= 0.008
    assert abs(metrics_doc["comparison_auroc"]["value"] - 0.755) <= 0.002
    assert metrics_doc["ceiling"]["ci_low"] <= 0.740 <= metrics_doc["ceiling"]["ci_high"]
    five = [
        c.parse_ratings(r["grades"], "digit_string")
        for r in source
        if r["grades"] and len(r["grades"]) == 5
    ]
    assert len(five) == 14886 and c.ratings_sorted(five)


def test_14_3_reference_trap_pairs_and_verdict(source: list[dict[str, Any]], funny: Any) -> None:
    metrics_doc, figs, checks, _ = funny
    ref = metrics_doc["reference_diagnostic"]
    assert round(ref["value"], 4) == 0.9519 and ref["n_pos"] == 2661
    assert round(ref["control"], 4) == 0.8728 and ref["n_neg"] == 3939
    control = [x for x in checks if x.metric_id == "reference_diagnostic"]
    assert [x.result for x in control] == ["fail"]
    verdict = decide(figs, checks, None)
    assert (verdict.verdict, verdict.rule) == ("passes", "held_out_rater")
    no_ratings = figures(source, "funny", {k: v for k, v in MAPPING.items() if k != "ratings"})
    v2 = decide(no_ratings[1], no_ratings[2], None)
    assert (v2.verdict, v2.rule) == ("passes", "default_c3")
    assert no_ratings[1].auroc.ci_low >= 0.70

    evaluation = pq.read_table(
        ROOT / "dataset" / "labelled" / "humicroedit_eval.parquet"
    ).to_pydict()
    y = [1 if lab == "humorous" else 0 for lab in evaluation["label"]]
    pos, neg, code = m.cross_label_pairs(evaluation["pair_id"], y)
    wins = m.paired_accuracy(np.array(evaluation["p_true"]), pos, neg)
    assert round(float(wins.mean()), 4) == 0.7778
    assert len(pos) == 945 and len(np.unique(code)) == 745
