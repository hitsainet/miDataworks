"""Paired score against the prototype, and report -> figures against run C (FTASKS 3.6, 3.7)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from src.services.detector_sets import paired, results_mapping

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "detector_sets"


def run1() -> dict[str, object]:
    return json.loads((FIXTURES / "paired_run1_l12.json").read_text())


def test_paired_score_reproduces_the_prototype_run_1_l12_figure() -> None:
    data = run1()
    labels = [v == "humorous" for v in data["label"]]  # type: ignore[union-attr]
    score = paired.paired_score(data["scores"], labels, data["pair_id"])  # type: ignore[arg-type]
    assert score is not None
    assert score.pairs == 945 and score.groups == 745
    assert score.paired == pytest.approx(0.7333, abs=5e-5)
    # The recorded interval [0.7049, 0.7645] came from one generator shared across scorers in the
    # prototype; an independent seeded draw reproduces it to within half a point.
    assert score.ci[0] == pytest.approx(0.7049, abs=0.005)
    assert score.ci[1] == pytest.approx(0.7645, abs=0.005)


def test_accept_source_rules() -> None:
    data = run1()
    labels = [v == "humorous" for v in data["label"]]  # type: ignore[union-attr]
    scores = np.asarray(data["scores"], dtype=float)
    ok = paired.accept_source("mistudio", scores, labels, reported_auroc=0.7086, reported_ci=None)
    assert ok.accepted, ok.reason
    far = paired.accept_source("mistudio", scores, labels, reported_auroc=0.7100, reported_ci=None)
    assert not far.accepted and "0.7100" in far.reason
    inside = paired.accept_source(
        "millm", scores, labels, reported_auroc=0.70, reported_ci=(0.6961, 0.7211)
    )
    assert inside.accepted
    outside = paired.accept_source(
        "millm", scores, labels, reported_auroc=0.75, reported_ci=(0.73, 0.76)
    )
    assert not outside.accepted
    shuffled = np.random.default_rng(0).permutation(scores)
    assert not paired.accept_source(
        "mistudio", shuffled, labels, reported_auroc=0.7086, reported_ci=None
    ).accepted


def test_ties_count_one_half() -> None:
    pos, neg, code = paired.pairs_of([True, False, True, False], ["g", "g", "h", "h"])
    wins = paired.paired_wins([1.0, 1.0, 2.0, 1.0], pos, neg)
    assert wins.tolist() == [0.5, 1.0]


# --- 3.7 report -> figures, run C --------------------------------------------------------------

TRAIN, TEST, OOD, CAL = (
    "pmd_eaa518e859d3",
    "pmd_fb14b0b206a5",
    "pmd_c99c8595671d",
    "pmd_7474e3562be6",
)


def run_c_figures(role_by_view: dict[str, dict[str, str]] | None = None) -> dict[str, object]:
    report = json.loads((FIXTURES / "report_pm_935fc9088482.json").read_text())
    names = {
        TEST: "Humor (JEV-9B) held-out test",
        OOD: "Humicroedit humor (human labels)",
    }
    roles = role_by_view or {
        TEST: {"role": "id_test", "role_id": "dsr_t", "version_id": "v1", "split": "test"},
        OOD: {"role": "ood_eval", "role_id": "dsr_o", "version_id": "v2", "split": "test"},
    }
    return results_mapping.figures_from_report(report, roles, names)


def test_run_c_reproduces_auroc_interval_rung_and_firing() -> None:
    fig = run_c_figures()
    ood = next(s for s in fig["sets"] if s["role"] == "ood_eval")  # type: ignore[union-attr,index]
    assert round(ood["auroc"], 4) == 0.7375
    assert [round(x, 4) for x in ood["ci"]] == [0.7258, 0.7502]
    assert (ood["n_positive"], ood["n_negative"]) == (2661, 3939)
    assert round(ood["firing"]["positives_firing"], 3) == 0.363
    assert round(ood["firing"]["negatives_firing"], 3) == 0.113
    assert fig["rung_language"] == "detects on unseen tasks"
    assert fig["rung_next_step"] == "run the judge baseline on the same out-of-distribution sets"
    assert "1% thresholds span" in fig["caveats"]["threshold_transfer_caution"]  # type: ignore[index]
    assert fig["caveats"]["validation_caveat"]["selection_maximised"] is True  # type: ignore[index]


def test_a_view_this_send_did_not_register_is_not_from_this_set() -> None:
    fig = run_c_figures(
        {OOD: {"role": "ood_eval", "role_id": "dsr_o", "version_id": "v2", "split": "test"}}
    )
    assert [s["probe_dataset_id"] for s in fig["not_from_this_set"]] == [TEST]  # type: ignore[union-attr,index]
    assert fig["not_from_this_set"][0]["label"] == "not from this set"  # type: ignore[index]


def test_firing_is_read_by_the_registered_view_name_only() -> None:
    report = json.loads((FIXTURES / "report_pm_935fc9088482.json").read_text())
    roles = {OOD: {"role": "ood_eval", "role_id": "dsr_o", "version_id": "v2", "split": "test"}}
    fig = results_mapping.figures_from_report(report, roles, {OOD: "Some other name"})
    assert fig["sets"][0]["firing"] is None  # type: ignore[index]


def test_the_rung_phrase_is_copied_never_composed() -> None:
    report = json.loads((FIXTURES / "report_pm_935fc9088482.json").read_text())
    report["rung_language"] = "words miStudio chose"
    report["probe"]["rung"] = 3
    fig = results_mapping.figures_from_report(report, {}, {})
    assert fig["rung_language"] == "words miStudio chose"
