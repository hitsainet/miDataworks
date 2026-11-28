"""One passing and one failing fixture per check (006 FTASKS 4.3, 4.4)."""

from __future__ import annotations

import numpy as np
import pytest

from src.services.calibration import ceiling as c
from src.services.calibration import checks as k
from src.services.calibration import metrics as m


def _ctx(y: np.ndarray, s: np.ndarray, **kw: object) -> k.CheckContext:
    keys = [f"{i:064x}" for i in range(len(y))]
    auroc = m.bootstrap_auroc_ci(y, s, resamples=100) if m.both_classes(y) else None
    fields: dict[str, object] = {
        "y": y,
        "scores": s,
        "row_keys": keys,
        "scores_by_key": dict(zip(keys, s.tolist(), strict=True)),
        "all_row_keys": keys,
        "auroc": auroc,
    }
    fields.update(kw)
    return k.CheckContext(**fields)  # type: ignore[arg-type]


def _signal(n: int = 400, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    y = rng.integers(0, 2, n)
    return y, y * 0.3 + rng.random(n)


def test_both_classes_pass_and_fail() -> None:
    y, s = _signal()
    assert k.both_classes(_ctx(y, s), "auroc").result == "pass"
    one = k.both_classes(_ctx(np.ones(5, dtype=int), np.arange(5.0)), "auroc")
    assert (
        one.result == "fail" and one.reason == "AUROC needs both classes; 0 negatives after mapping"
    )


def test_permutation_null_passes_on_aligned_scores() -> None:
    y, s = _signal()
    out = k.label_permutation_null(_ctx(y, s), "auroc")
    assert out.result == "pass" and out.statistic["low"] < 0.5 < out.statistic["high"]


def test_permutation_null_fails_when_the_null_excludes_one_half(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    y, s = _signal()
    monkeypatch.setattr(k, "auroc_fast", lambda yy, ss: 0.9)  # a leak: every permutation 0.9
    out = k.label_permutation_null(_ctx(y, s), "auroc")
    assert out.result == "fail" and "excludes 0.5" in (out.reason or "")


def test_row_alignment_pass_and_fail() -> None:
    y, s = _signal()
    good = _ctx(y, s)
    assert k.row_alignment(good, "auroc").result == "pass"
    shuffled = dict(zip(good.row_keys, np.roll(s, 1).tolist(), strict=True))
    bad = _ctx(y, s, scores_by_key=shuffled)
    out = k.row_alignment(bad, "auroc")
    assert out.result == "fail" and out.statistic["reported"] != out.statistic["recomputed"]


def test_row_alignment_fails_on_a_missing_key() -> None:
    y, s = _signal(20)
    ctx = _ctx(y, s, scores_by_key={})
    assert k.row_alignment(ctx, "auroc").result == "fail"


def _ceiling_ctx(fixed: bool, near_perfect: bool = False) -> k.CheckContext:
    rng = np.random.default_rng(0)
    rows = [rng.integers(0, 4, 5).tolist() for _ in range(300)]
    R, counts = c.ratings_matrix(rows)
    draws = c.held_out_draws(R, counts, positive_at_or_above=1.6, negative_at_or_below=0.4, draws=4)
    if fixed:
        draws = [c.Draw(d.kept, d.held, d.consensus, np.zeros_like(d.positions)) for d in draws]
    scores = rng.random(300)
    result = c.ceiling_and_comparison(draws, scores, n_rows=300, resamples=30)
    assert result is not None
    if near_perfect:
        result = c.CeilingResult(
            m.Interval(0.995, 0.99, 1.0, 100, 30, 0),
            result.comparison,
            result.draw_values,
            result.comparison_values,
            result.n_mean,
            result.draws,
        )
    y, s = _signal(300)
    keys = [f"{i:064x}" for i in range(300)]
    return _ctx(
        y,
        s,
        ceiling=result,
        draws=draws,
        all_row_keys=keys,
        scores_by_key=dict(zip(keys, scores.tolist(), strict=True)),
    )


def test_circularity_pass_fixed_position_fail_and_near_perfect_fail() -> None:
    assert k.ceiling_circularity(_ceiling_ctx(False), "ceiling").result == "pass"
    fixed = k.ceiling_circularity(_ceiling_ctx(True), "ceiling")
    assert fixed.result == "fail" and "same position" in (fixed.reason or "")
    near = k.ceiling_circularity(_ceiling_ctx(False, near_perfect=True), "ceiling")
    assert near.result == "fail" and "own consensus" in (near.reason or "")


def test_comparison_row_alignment_pass() -> None:
    ctx = _ceiling_ctx(False)
    assert k.row_alignment(ctx, "comparison_auroc").result == "pass"


def test_shortcut_not_applicable_pass_and_fail() -> None:
    y, s = _signal()
    na = k.shortcut_comparison(_ctx(y, s), "auroc")
    assert na.result == "not_applicable" and "004" in (na.reason or "")
    assert k.shortcut_comparison(_ctx(y, s, shortcut={"source": 0.51}), "auroc").result == "pass"
    out = k.shortcut_comparison(_ctx(y, s, shortcut={"source": 0.99}), "auroc")
    assert out.result == "fail" and "'source'" in (out.reason or "")


def test_negative_class_control_reference_pass_and_fail() -> None:
    y, s = _signal(10)
    good = m.ReferenceWins(np.ones(40), np.arange(40), np.array([0.0, 1.0] * 20), np.arange(40))
    assert (
        k.negative_class_control(_ctx(y, s, reference=good), "reference_diagnostic").result
        == "pass"
    )
    bad = m.ReferenceWins(np.ones(40), np.arange(40), np.ones(40), np.arange(40))
    out = k.negative_class_control(_ctx(y, s, reference=bad), "reference_diagnostic")
    assert out.result == "fail" and out.statistic["control"] == 1.0


def test_negative_class_control_paired_pass_and_fail() -> None:
    y, s = _signal(10)
    wins = np.ones(30)
    interval = m.cluster_bootstrap_ci(wins, np.arange(30), resamples=50)
    good = k.PairedFigure(wins, np.arange(30), interval)
    assert k.negative_class_control(_ctx(y, s, paired=good), "paired").result == "pass"
    bad = k.PairedFigure(np.zeros(30), np.arange(30), interval)
    assert k.negative_class_control(_ctx(y, s, paired=bad), "paired").result == "fail"


def test_a_failed_check_marks_its_metric() -> None:
    """FR-006.16: the record's check list names the metric whose check failed."""
    y, s = _signal(10)
    bad = m.ReferenceWins(np.ones(40), np.arange(40), np.ones(40), np.arange(40))
    results = [
        k.negative_class_control(_ctx(y, s, reference=bad), "reference_diagnostic"),
        k.both_classes(_ctx(y, s), "auroc"),
    ]
    assert k.failed_metrics(results) == {"reference_diagnostic"}
