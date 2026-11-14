"""The shortcut rule's pure functions, with hand-computed expectations (FTASKS 3.4, 4.1, 5.1)."""

from __future__ import annotations

import numpy as np
import pytest

from src.services.curation import shortcut_rules as r


class TestMajorityByValue:
    def test_each_value_predicts_its_training_majority(self) -> None:
        x_train = np.array([0, 0, 0, 1, 1, 1])
        y_train = np.array([1, 1, 0, 0, 0, 1])
        assert r.majority_by_value(x_train, y_train, np.array([0, 1]), 2, 2).tolist() == [1, 0]

    def test_ties_go_to_the_lowest_label_code(self) -> None:
        x_train = np.array([0, 0])
        y_train = np.array([1, 0])
        assert r.majority_by_value(x_train, y_train, np.array([0]), 2, 1).tolist() == [0]

    def test_unseen_value_predicts_the_training_majority(self) -> None:
        x_train = np.array([0, 0, 0])
        y_train = np.array([1, 1, 0])  # majority 1 overall
        # value 2 never appears in training: it falls back to the training majority (1),
        # NOT to its own label (it has none in training).
        assert r.majority_by_value(x_train, y_train, np.array([2]), 2, 3).tolist() == [1]

    def test_unseen_fallback_is_the_training_majority_not_a_fixed_class(self) -> None:
        x_train = np.array([0, 0, 0])
        y_train = np.array([0, 0, 1])  # majority 0
        assert r.majority_by_value(x_train, y_train, np.array([1]), 2, 2).tolist() == [0]


class TestHeldOut:
    def test_unique_id_reads_chance_held_out(self) -> None:
        rng = np.random.default_rng(1)
        y = rng.permutation(np.repeat([0, 1], 500))
        x = np.arange(1000)  # one value per row
        figure = r.balanced_accuracy_cv(x, y, 2, folds=5, seed=3)
        assert abs(figure - 0.5) < 0.02
        # in-sample, the same column would score 100%: the held-out path is the one used
        in_sample = r.majority_by_value(x, y, x, 2, 1000)
        assert (in_sample == y).mean() == 1.0

    def test_perfect_predictor_reads_one(self) -> None:
        y = np.repeat([0, 1], 50)
        assert r.balanced_accuracy_cv(y.copy(), y, 2, folds=5, seed=1) == 1.0

    def test_seeded_folds_are_reproducible_and_depend_on_the_seed(self) -> None:
        y = np.repeat([0, 1], 20)
        assert r.fold_ids(y, 5, 7).tolist() == r.fold_ids(y, 5, 7).tolist()
        assert r.fold_ids(y, 5, 7).tolist() != r.fold_ids(y, 5, 8).tolist()

    def test_balanced_accuracy_reads_chance_on_an_imbalanced_constant_column(self) -> None:
        y = np.array([0] * 90 + [1] * 10)
        x = np.zeros(100, dtype=np.int64)
        assert r.balanced_accuracy_cv(x, y, 2, folds=5, seed=2) == pytest.approx(0.5)


class TestControl:
    def test_control_reads_chance_on_a_real_shortcut(self) -> None:
        y = np.repeat([0, 1], 200)
        control = r.permuted_control(y.copy(), y, 2, folds=5, seed=4, runs=5)
        assert abs(control - 0.5) < 0.05

    def test_validity_tolerance(self) -> None:
        assert r.control_is_valid(0.52, 0.5, 2) is True
        assert r.control_is_valid(0.53, 0.5, 2) is False  # control 53% at 2 pp: invalid
        assert r.control_is_valid(0.40, 0.5, 2) is True  # below chance stays valid

    def test_tolerance_never_below_the_controls_own_noise(self) -> None:
        # 200 rows, 5 runs: noise floor 300*sqrt(.25/1000) = 4.74 points
        assert r.control_noise_pp(0.5, 200, 5) == pytest.approx(4.743, abs=0.01)
        assert r.control_is_valid(0.53, 0.5, 2, n_rows=200, runs=5) is True
        assert r.control_is_valid(0.55, 0.5, 2, n_rows=200, runs=5) is False
        # on the reference pool the configured 2 points governs
        assert r.control_noise_pp(0.5, 10_914, 5) < 2
        assert r.control_is_valid(0.53, 0.5, 2, n_rows=10_914, runs=5) is False


class TestDecideWarning:
    def test_default_margin_is_p19(self) -> None:
        assert r.DEFAULT_SHORTCUT_MARGIN_PP == 10

    def test_exactly_chance_plus_margin_warns(self) -> None:
        assert r.decide_warning(0.6, 0.5, 0.5, 10, True) is True

    def test_just_below_is_silent(self) -> None:
        assert r.decide_warning(0.599, 0.5, 0.5, 10, True) is False

    def test_control_half_of_rule(self) -> None:
        # 64% clears chance + 10 (60%) but not control + 10 (65%): silent (P-19, S3-06).
        assert r.decide_warning(0.64, 0.5, 0.55, 10, True) is False
        assert r.decide_warning(0.66, 0.5, 0.55, 10, True) is True

    def test_chance_half_of_rule(self) -> None:
        # a below-chance control does not lower the bar under chance + margin
        assert r.decide_warning(0.55, 0.5, 0.40, 10, True) is False

    def test_invalid_never_warns(self) -> None:
        assert r.decide_warning(0.99, 0.5, 0.53, 10, False) is False

    def test_humor_figures_at_the_default(self) -> None:
        m = r.DEFAULT_SHORTCUT_MARGIN_PP
        assert r.decide_warning(0.885, 0.5, 0.5, m, True) is True
        assert r.decide_warning(0.500, 0.5, 0.5, m, True) is False


def test_score_column_wires_figure_control_and_validity() -> None:
    y = np.repeat([0, 1], 100)
    folds = r.fold_ids(y, 5, 9)
    perms = r.permutations(y, folds=5, seed=9, runs=3)
    score = r.score_column(
        y.copy(), y, 2, folds=5, seed=9, tolerance_pp=2, fold_index=folds, permuted=perms
    )
    assert score.figure == 1.0
    assert abs(score.control - 0.5) < 0.1
    assert score.valid is r.control_is_valid(score.control, 0.5, 2, n_rows=200, runs=3)


def test_score_column_reads_the_control_from_the_permutations_it_is_given() -> None:
    # An "identity permutation" makes the control equal the figure: the column must be invalid.
    # A score_column that ignored its permutations (or assumed chance) would call it valid.
    y = np.repeat([0, 1], 100)
    folds = r.fold_ids(y, 5, 9)
    score = r.score_column(
        y.copy(), y, 2, folds=5, seed=9, tolerance_pp=2, fold_index=folds, permuted=[(y, folds)]
    )
    assert score.control == 1.0 and score.valid is False
