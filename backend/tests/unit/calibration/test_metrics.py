"""Known-answer tests for the pure metrics (006 FTASKS 3.2 – 3.8).

The prototype's own test shapes are carried over: ``scripts/test_validate_judge.py`` (perfect,
inverted, own-reference, each-with-its-own-reference) and ``scripts/test_paired_probe.py``
(cross-label within one group only; ties score one half).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import roc_auc_score

from src.services.calibration import metrics as m

# --- 3.2 AUROC --------------------------------------------------------------------------------


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_auroc_fast_equals_scikit_learn_with_ties(seed: int) -> None:
    rng = np.random.default_rng(seed)
    y = rng.integers(0, 2, 500)
    s = np.round(rng.random(500), 1)  # heavy ties
    assert abs(m.auroc_fast(y, s) - roc_auc_score(y, s)) < 1e-12


def test_auroc_refuses_one_class() -> None:
    with pytest.raises(ValueError, match="both classes"):
        m.auroc(np.array([1, 1, 1]), np.array([0.1, 0.2, 0.3]))
    with pytest.raises(ValueError, match="both classes"):
        m.auroc_fast(np.array([0, 0]), np.array([0.1, 0.2]))


def test_bootstrap_drops_and_counts_single_class_resamples() -> None:
    # One positive among three rows: P(no positive in a resample) = (2/3)^3 ~ 0.30.
    y = np.array([1, 0, 0])
    p = np.array([0.9, 0.1, 0.2])
    interval = m.bootstrap_auroc_ci(y, p, seed=5, resamples=400)
    assert interval.value == 1.0
    assert 60 < interval.dropped < 200
    assert interval.resamples == 400 and interval.n == 3


def test_bootstrap_reproduces_its_own_seed() -> None:
    rng = np.random.default_rng(9)
    y = rng.integers(0, 2, 200)
    p = rng.random(200)
    a = m.bootstrap_auroc_ci(y, p, seed=11, resamples=300)
    b = m.bootstrap_auroc_ci(y, p, seed=11, resamples=300)
    assert a == b
    assert a.ci_low <= a.value <= a.ci_high


def test_bootstrap_draws_rows_like_the_prototype() -> None:
    """The prototype's draw sequence: a fresh generator, integers(0, n, n) per resample."""
    rng = np.random.default_rng(3)
    y = rng.integers(0, 2, 120)
    p = rng.random(120)
    expected_rng = np.random.default_rng(20261004)
    stats = []
    for _ in range(50):
        idx = expected_rng.integers(0, 120, 120)
        stats.append(roc_auc_score(y[idx], p[idx]))
    lo, hi = np.quantile(stats, [0.025, 0.975])
    got = m.bootstrap_auroc_ci(y, p, resamples=50)
    assert (got.ci_low, got.ci_high) == (lo, hi)


# --- 3.3 the four validate_judge cases --------------------------------------------------------


def _frame(p_funny: float, p_notfunny: float, p_original: float | dict[str, float]):
    group, is_ref, label, score = [], [], [], []
    for pair in ("h1", "h2"):
        original = p_original[pair] if isinstance(p_original, dict) else p_original
        for lab, ref, s in ((1, False, p_funny), (0, False, p_notfunny), (None, True, original)):
            group.append(pair)
            is_ref.append(ref)
            label.append(lab)
            score.append(s)
    return group, is_ref, label, np.array(score)


def _auroc_of(label: list[int | None], score: np.ndarray) -> float:
    keep = [i for i, v in enumerate(label) if v is not None]
    return m.auroc(np.array([label[i] for i in keep]), score[keep])


def test_a_perfect_labeler() -> None:
    group, is_ref, label, score = _frame(0.9, 0.1, 0.05)
    assert _auroc_of(label, score) == 1.0
    wins = m.reference_pairs(group, is_ref, label, score)
    assert wins.positive.mean() == 1.0


def test_an_inverted_labeler() -> None:
    group, is_ref, label, score = _frame(0.1, 0.9, 0.95)
    assert _auroc_of(label, score) == 0.0
    assert m.reference_pairs(group, is_ref, label, score).positive.mean() == 0.0


def test_the_reference_diagnostic_compares_with_the_rows_own_reference() -> None:
    """Positives beat negatives (AUROC 1) but NOT their own references: diagnostic 0."""
    group, is_ref, label, score = _frame(0.6, 0.1, 0.7)
    assert _auroc_of(label, score) == 1.0
    assert m.reference_pairs(group, is_ref, label, score).positive.mean() == 0.0


def test_each_row_is_compared_with_its_own_reference_not_any() -> None:
    group, is_ref, label, score = _frame(0.6, 0.1, {"h1": 0.7, "h2": 0.3})
    assert m.reference_pairs(group, is_ref, label, score).positive.mean() == 0.5


# --- 3.4 pairs --------------------------------------------------------------------------------


def _paired_frame() -> tuple[list[str], list[int | None]]:
    # A: 2 positive, 1 negative -> 2 pairs; B: 1 positive, 2 negative -> 2 pairs; C: positive only
    return (["A", "A", "A", "B", "B", "B", "C"], [1, 1, 0, 1, 0, 0, 1])


def test_pairs_are_cross_label_within_one_group_only() -> None:
    group, label = _paired_frame()
    pos, neg, code = m.cross_label_pairs(group, label)
    assert sorted(zip(pos.tolist(), neg.tolist(), strict=True)) == [(0, 2), (1, 2), (3, 4), (3, 5)]
    assert len(set(code.tolist())) == 2


def test_accuracy_counts_wins_ties_and_losses() -> None:
    group, label = _paired_frame()
    pos, neg, _ = m.cross_label_pairs(group, label)
    scores = np.array([5.0, 1.0, 1.0, 0.0, 2.0, 0.0, 9.0])
    wins = dict(
        zip(
            zip(pos.tolist(), neg.tolist(), strict=True),
            m.paired_accuracy(scores, pos, neg).tolist(),
            strict=True,
        )
    )
    assert wins == {(0, 2): 1.0, (1, 2): 0.5, (3, 4): 0.0, (3, 5): 0.5}


def test_a_pair_whose_stratum_differs_is_not_formed() -> None:
    group, label = _paired_frame()
    strata = ["edited", "original", "edited", "x", "x", "y", "z"]
    pos, neg, _ = m.cross_label_pairs(group, label, strata)
    assert sorted(zip(pos.tolist(), neg.tolist(), strict=True)) == [(0, 2), (3, 4)]


def test_rows_without_a_group_form_no_pair() -> None:
    pos, _, _ = m.cross_label_pairs([None, None], [1, 0])
    assert len(pos) == 0


def test_cluster_bootstrap_resamples_groups_not_pairs() -> None:
    """One group of 9 winning pairs and nine groups of one losing pair each: resampling GROUPS
    gives a much wider interval than resampling pairs would."""
    wins = np.array([1.0] * 9 + [0.0] * 9)
    code = np.array([0] * 9 + list(range(1, 10)))
    interval = m.cluster_bootstrap_ci(wins, code, seed=1, resamples=500)
    rng = np.random.default_rng(1)
    pair_stats = [wins[rng.integers(0, 18, 18)].mean() for _ in range(500)]
    pair_width = np.quantile(pair_stats, 0.975) - np.quantile(pair_stats, 0.025)
    assert interval.value == 0.5
    assert interval.ci_high - interval.ci_low > pair_width + 0.1


# --- 3.5 reference control ----------------------------------------------------------------------


def test_the_control_can_fail_when_negatives_beat_their_references_too() -> None:
    """Positives AND negatives sit above their reference: the 0.873 trap in miniature."""
    group, is_ref, label, score = [], [], [], []
    for g in range(40):
        for lab, ref, s in ((1, False, 0.6), (0, False, 0.5), (None, True, 0.1)):
            group.append(f"g{g:02d}")
            is_ref.append(ref)
            label.append(lab)
            score.append(s)
    wins = m.reference_pairs(group, is_ref, label, np.array(score))
    assert wins.positive.mean() == 1.0 and wins.negative.mean() == 1.0
    control = m.cluster_bootstrap_ci(wins.negative, wins.negative_group, seed=1, resamples=200)
    assert control.ci_low > 0.5


# --- 3.6 reliability ----------------------------------------------------------------------------


def test_bins_equal_pd_cut_on_values_exactly_on_edges() -> None:
    p = np.array([0.0, 0.1, 0.2, 0.3, 0.30000000000000004, 0.5, 0.7, 0.9, 1.0, 0.05, 0.95])
    ours = m.bin_index(p)
    theirs = pd.cut(pd.Series(p), np.linspace(0, 1, 11), include_lowest=True, labels=False)
    assert ours.tolist() == theirs.astype(int).tolist()


def test_reliability_counts_and_flags_sparse_bins() -> None:
    p = np.array([0.05] * 40 + [0.95] * 5)
    y = np.array([0] * 30 + [1] * 10 + [1] * 5)
    bins = m.reliability_bins(y, p)
    assert len(bins) == 10
    assert bins[0]["n"] == 40 and bins[0]["positive_fraction"] == 0.25 and not bins[0]["sparse"]
    assert bins[9]["n"] == 5 and bins[9]["sparse"]
    assert bins[4]["n"] == 0 and bins[4]["positive_fraction"] is None


# --- 3.7 bands ----------------------------------------------------------------------------------


def test_bands_are_inclusive_at_both_thresholds() -> None:
    p = np.array([0.8, 0.79999, 0.2, 0.20001, 0.5])
    y = np.array([1, 1, 0, 0, 1])
    out = m.band_shares(y, p, threshold_positive=0.8, threshold_negative=0.2)
    assert out["overall"]["at_or_above"] == pytest.approx(1 / 5)
    assert out["overall"]["at_or_below"] == pytest.approx(1 / 5)
    assert out["overall"]["excluded"] == pytest.approx(3 / 5)
    assert out["by_class"]["positive"]["n"] == 3
    assert out["by_class"]["negative"]["at_or_below"] == pytest.approx(0.5)


# --- 3.8 kappa and multi-class ------------------------------------------------------------------


def test_kappa_counts_only_kept_rows() -> None:
    out = m.kappa_on_kept(["a", "b", None, "a"], ["a", "b", "b", "b"])
    assert out is not None and out["n_kept"] == 3
    assert out["value"] == pytest.approx(0.4)


def test_macro_auroc_one_versus_rest_with_known_per_class_values() -> None:
    human = ["x", "y", "z", "x", "y", "z"]
    dist = [
        {"x": 0.9, "y": 0.05, "z": 0.05},
        {"x": 0.1, "y": 0.8, "z": 0.1},
        {"x": 0.2, "y": 0.1, "z": 0.7},
        {"x": 0.3, "y": 0.4, "z": 0.3},  # x scored low: x's AUROC falls below 1
        {"x": 0.1, "y": 0.7, "z": 0.2},
        {"x": 0.1, "y": 0.1, "z": 0.8},
    ]
    out = m.macro_auroc_ovr(human, dist, ["x", "y", "z"])
    assert out["by_class"]["y"] == 1.0 and out["by_class"]["z"] == 1.0
    assert out["by_class"]["x"] == pytest.approx(
        roc_auc_score([1, 0, 0, 1, 0, 0], [0.9, 0.1, 0.2, 0.3, 0.1, 0.1])
    )
    assert out["macro"] == pytest.approx((out["by_class"]["x"] + 2) / 3)
