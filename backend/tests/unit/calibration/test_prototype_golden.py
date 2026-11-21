"""Golden parity with the prototype's own code (006 FTASKS 3.11; FTID 006 section 8).

``prototype_golden.json`` holds synthetic inputs and the outputs of ``validate_judge.metrics``,
``paired_probe.pairs_of``, ``paired_accuracy`` and ``cluster_ci`` run on them by
``backend/scripts/make_calibration_golden.py``. The prototype rounds its outputs to four places,
so each figure here is rounded the same way and compared for EQUALITY, not closeness.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from src.services.calibration import metrics as m

GOLDEN = json.loads(
    (
        Path(__file__).resolve().parents[2] / "fixtures" / "calibration" / "prototype_golden.json"
    ).read_text()
)
CASES = GOLDEN["cases"]


def r4(x: float) -> float:
    return round(float(x), 4)


def ends_of(rows: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray, list[str]]:
    ends = [r for r in rows if r["kind"] == "edited" and r["human_label"] is not None]
    return (
        np.array([int(r["human_label"]) for r in ends]),
        np.array([float(r["p_true"]) for r in ends]),
        [r["pair_id"] for r in ends],
    )


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"seed{c['seed']}")
def test_auroc_and_its_interval(case: dict[str, Any]) -> None:
    y, p, _ = ends_of(case["rows"])
    interval = m.bootstrap_auroc_ci(y, p)
    assert r4(interval.value) == case["validate_judge"]["auroc_confident_ends"]
    assert [r4(interval.ci_low), r4(interval.ci_high)] == case["validate_judge"]["auroc_ci95"]
    assert interval.dropped == 0


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"seed{c['seed']}")
def test_reliability(case: dict[str, Any]) -> None:
    y, p, _ = ends_of(case["rows"])
    ours = [b for b in m.reliability_bins(y, p) if b["n"]]
    theirs = case["validate_judge"]["reliability"]
    assert [b["n"] for b in ours] == [b["n"] for b in theirs]
    assert [r4(b["positive_fraction"]) for b in ours] == [b["human_funny_fraction"] for b in theirs]


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"seed{c['seed']}")
def test_bands(case: dict[str, Any]) -> None:
    edited = [r for r in case["rows"] if r["kind"] == "edited"]
    p = np.array([float(r["p_true"]) for r in edited])
    out = m.band_shares(np.zeros(len(p)), p, threshold_positive=0.8, threshold_negative=0.2)
    theirs = case["validate_judge"]["p_bands"]["edited"]
    assert r4(out["overall"]["at_or_above"]) == theirs["p>=0.8"]
    assert r4(out["overall"]["at_or_below"]) == theirs["p<=0.2"]
    assert r4(out["overall"]["excluded"]) == theirs["excluded_0.2_0.8"]


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"seed{c['seed']}")
def test_reference_diagnostic(case: dict[str, Any]) -> None:
    """The prototype's 'paired accuracy' is the reference diagnostic (FR-006.41)."""
    rows = case["rows"]
    wins = m.reference_pairs(
        [r["pair_id"] for r in rows],
        [r["kind"] == "original" for r in rows],
        [
            None if r["kind"] != "edited" or r["human_label"] is None else int(r["human_label"])
            for r in rows
        ],
        np.array([float(r["p_true"]) for r in rows]),
    )
    assert r4(wins.positive.mean()) == case["validate_judge"]["paired_accuracy"]


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"seed{c['seed']}")
def test_pairs_paired_accuracy_and_cluster_interval(case: dict[str, Any]) -> None:
    y, p, groups = ends_of(case["rows"])
    pos, neg, code = m.cross_label_pairs(groups, y.tolist())
    assert (
        sorted([int(a), int(b)] for a, b in zip(pos, neg, strict=True))
        == case["paired_probe"]["pairs"]
    )
    wins = m.paired_accuracy(p, pos, neg)
    assert r4(wins.mean()) == case["paired_probe"]["paired"]
    interval = m.cluster_bootstrap_ci(wins, code)
    assert [r4(interval.ci_low), r4(interval.ci_high)] == case["paired_probe"]["paired_ci95"]
