"""The held-out rater ceiling (006 FTASKS 3.9, 3.10; FTDD 006 section 4.4)."""

from __future__ import annotations

import inspect

import numpy as np
import pytest
from sklearn.metrics import roc_auc_score

from src.services.calibration import ceiling as c

# --- 3.9 parsing and sortedness -----------------------------------------------------------------


def test_parse_digit_string_and_list() -> None:
    assert c.parse_ratings("33110", "digit_string") == [3, 3, 1, 1, 0]
    assert c.parse_ratings([2, 0, 1], "list") == [2, 0, 1]
    assert c.parse_ratings(None, "list") is None
    # Empty means missing: Humicroedit's original rows carry grades == "" (live run 2026-10-07).
    assert c.parse_ratings("", "digit_string") is None
    assert c.parse_ratings([], "list") is None


@pytest.mark.parametrize(
    ("value", "fmt"),
    [
        ("3a1", "digit_string"),
        (311, "digit_string"),
        ([1, "2"], "list"),
        ([True], "list"),
        ("1", "csv"),
    ],
)
def test_parse_refuses_other_types(value: object, fmt: str) -> None:
    with pytest.raises(ValueError):
        c.parse_ratings(value, fmt)


def test_sorted_unsorted_and_mixed_lengths() -> None:
    assert c.ratings_sorted([[3, 3, 1], [2, 0], [1], None])  # all non-increasing
    assert c.ratings_sorted([[0, 1, 3], [1, 1]])  # all non-decreasing
    assert not c.ratings_sorted([[3, 1, 2], [2, 1]])
    assert not c.ratings_sorted([[3, 1], [1, 3]])  # mixed directions
    assert not c.ratings_sorted([[1], None])  # nothing to judge


# --- 3.10 draws and the ceiling ----------------------------------------------------------------


def test_held_out_draws_has_no_position_parameter() -> None:
    params = inspect.signature(c.held_out_draws).parameters
    assert not {p for p in params if "pos" in p.lower() and "positive" not in p.lower()}
    assert set(params) == {
        "ratings",
        "counts",
        "positive_at_or_above",
        "negative_at_or_below",
        "draws",
        "seed",
    }


def test_rows_with_fewer_than_three_ratings_are_excluded() -> None:
    R, counts = c.ratings_matrix([[3, 3, 3], [0, 0], [0, 0, 0, 0], [3, 3]])
    draws = c.held_out_draws(R, counts, positive_at_or_above=1.6, negative_at_or_below=0.4, draws=5)
    for d in draws:
        assert set(d.kept.tolist()) <= {0, 2}
        assert len(d.positions) == 2


def test_a_known_three_rater_fixture() -> None:
    """Row 0: (3,3,0): hold out 0 -> rest mean 3 (positive), hold out a 3 -> rest 1.5 (excluded).
    Row 1: (0,0,0): always negative with held 0. Row 2: (3,3,3): always positive with held 3."""
    R, counts = c.ratings_matrix([[3, 3, 0], [0, 0, 0], [3, 3, 3]])
    draws = c.held_out_draws(
        R, counts, positive_at_or_above=1.6, negative_at_or_below=0.4, draws=30, seed=4
    )
    saw_zero_held_out = saw_three_held_out = False
    for d in draws:
        kept = d.kept.tolist()
        assert 1 in kept and 2 in kept
        assert d.consensus[kept.index(1)] == 0 and d.held[kept.index(1)] == 0
        assert d.consensus[kept.index(2)] == 1 and d.held[kept.index(2)] == 3
        if 0 in kept:
            saw_zero_held_out = True
            assert d.held[kept.index(0)] == 0 and d.consensus[kept.index(0)] == 1
        else:
            saw_three_held_out = True
    assert saw_zero_held_out and saw_three_held_out


def test_the_comparison_uses_each_draws_rows_and_labels() -> None:
    rng = np.random.default_rng(0)
    n = 300
    rows = [list(rng.integers(0, 4, 5)) for _ in range(n)]
    R, counts = c.ratings_matrix(rows)
    draws = c.held_out_draws(R, counts, positive_at_or_above=1.6, negative_at_or_below=0.4, draws=6)
    scores = rng.random(n)
    out = c.ceiling_and_comparison(draws, scores, n_rows=n, resamples=50)
    assert out is not None
    expected_comp = np.mean([roc_auc_score(d.consensus, scores[d.kept]) for d in draws])
    expected_ceil = np.mean([roc_auc_score(d.consensus, d.held) for d in draws])
    assert out.comparison.value == pytest.approx(expected_comp, abs=1e-12)
    assert out.ceiling.value == pytest.approx(expected_ceil, abs=1e-12)
    assert out.ceiling.ci_low <= out.ceiling.value <= out.ceiling.ci_high


def test_weighted_auroc_equals_auroc_on_the_expanded_resample() -> None:
    rng = np.random.default_rng(2)
    y = rng.integers(0, 2, 80)
    s = np.round(rng.random(80), 1)
    idx = rng.integers(0, 80, 80)
    w = np.bincount(idx, minlength=80)
    assert c.weighted_auroc(y, s, w) == pytest.approx(roc_auc_score(y[idx], s[idx]), abs=1e-12)


def test_sorted_ratings_make_a_fixed_position_circular_but_random_draws_do_not() -> None:
    """Every row sorted descending: holding out position 1 of 3 equal-ish raters is circular."""
    rng = np.random.default_rng(1)
    rows = [sorted(rng.integers(0, 4, 5).tolist(), reverse=True) for _ in range(600)]
    assert c.ratings_sorted(rows)
    R, counts = c.ratings_matrix(rows)
    draws = c.held_out_draws(R, counts, positive_at_or_above=1.6, negative_at_or_below=0.4)
    # random draws vary the held position across rows
    assert all(len(np.unique(d.positions)) > 1 for d in draws)
