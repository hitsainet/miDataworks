"""The shortcut rule: held-out balanced accuracy, the permuted control, the warning (FTDD 004 §6.3).

Pure. Decides warnings; never reads settings; never sees row text. Every argument is an integer
code array or a number, so a test of these functions is a test of what ships, and each has one
production caller (``test_curation_call_sites.py``).

- ``majority_by_value``: the predictor "this column alone" describes. Each value predicts the
  majority label of that value in the training rows; a value never seen in training predicts the
  training majority; ties go to the lowest label code.
- ``balanced_accuracy_cv``: that predictor's pooled out-of-fold balanced accuracy over seeded
  stratified folds. Held out, because an id column scores 100% in-sample (FR-004.30).
- ``permuted_control``: the same figure with the label permuted, averaged over seeded runs. It must
  read chance within a tolerance, or the column's figure is invalid (FR-004.31).
- ``decide_warning``: P-19 as confirmed at S3-06 — warn when the figure is valid AND at least the
  margin above BOTH chance and the control ("whichever is higher").
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from sklearn.metrics import balanced_accuracy_score
from sklearn.model_selection import StratifiedKFold

#: P-19: the default warning margin in percentage points above chance and the control.
DEFAULT_SHORTCUT_MARGIN_PP = 10


def majority_by_value(
    x_train: np.ndarray, y_train: np.ndarray, x_test: np.ndarray, k: int, n_values: int
) -> np.ndarray:
    """Predict each test row's label from its value's training majority (FTID 004 §7.1)."""
    counts = np.bincount(
        x_train.astype(np.int64) * k + y_train.astype(np.int64), minlength=n_values * k
    ).reshape(n_values, k)
    fallback = int(np.argmax(np.bincount(y_train.astype(np.int64), minlength=k)))
    per_value = np.argmax(counts, axis=1)  # argmax takes the first maximum: lowest code on ties
    per_value[counts.sum(axis=1) == 0] = fallback  # unseen in training -> training majority
    result: np.ndarray = per_value[x_test.astype(np.int64)]
    return result


def fold_ids(y: np.ndarray, folds: int, seed: int) -> np.ndarray:
    """The fold of every row under seeded stratified K-fold. Depends only on ``y`` and ``seed``."""
    out = np.empty(len(y), dtype=np.int32)
    splitter = StratifiedKFold(n_splits=folds, shuffle=True, random_state=seed)
    for index, (_, test) in enumerate(splitter.split(np.zeros(len(y)), y)):
        out[test] = index
    return out


def balanced_accuracy_cv(
    x: np.ndarray,
    y: np.ndarray,
    k: int,
    *,
    folds: int,
    seed: int,
    fold_index: np.ndarray | None = None,
) -> float:
    """Pooled out-of-fold balanced accuracy of :func:`majority_by_value` on column ``x``.

    ``fold_index`` may be passed when several columns share one label array (the folds depend only
    on ``y`` and ``seed``), so the splitter runs once per audit, not once per column.
    """
    assignment = fold_ids(y, folds, seed) if fold_index is None else fold_index
    n_values = int(x.max()) + 1 if len(x) else 1
    pred = np.empty_like(y)
    for fold in range(folds):
        test = assignment == fold
        train = ~test
        pred[test] = majority_by_value(x[train], y[train], x[test], k, n_values)
    return float(balanced_accuracy_score(y, pred))


def permuted_control(
    x: np.ndarray,
    y: np.ndarray,
    k: int,
    *,
    folds: int,
    seed: int,
    runs: int,
    permuted: list[tuple[np.ndarray, np.ndarray]] | None = None,
) -> float:
    """Mean figure over ``runs`` seeded label permutations (FR-004.31).

    ``permuted`` may carry precomputed ``(permuted_y, fold_index)`` pairs shared across columns;
    they must be exactly what :func:`permutations` returns for the same arguments.
    """
    pairs = permuted if permuted is not None else permutations(y, folds=folds, seed=seed, runs=runs)
    return float(
        np.mean(
            [
                balanced_accuracy_cv(x, py, k, folds=folds, seed=seed, fold_index=pf)
                for py, pf in pairs
            ]
        )
    )


def permutations(
    y: np.ndarray, *, folds: int, seed: int, runs: int
) -> list[tuple[np.ndarray, np.ndarray]]:
    """The control's permuted labels and their folds: run ``i`` permutes with the seeded generator
    and folds with ``seed + i + 1`` (FTID 004 §7.1)."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(runs):
        py = rng.permutation(y)
        out.append((py, fold_ids(py, folds, seed + i + 1)))
    return out


def control_noise_pp(chance: float, n_rows: int, runs: int) -> float:
    """Three standard errors of a mean-of-``runs`` null figure on ``n_rows`` rows, in points.

    A permuted control is itself a noisy estimate: on 200 rows one run's standard error is about
    3.5 points, so a fixed 2-point tolerance marks honest columns invalid about one time in ten
    (found by 008's seam test, defect D2). The tolerance used is the larger of the configured one
    and this noise floor; on the 10,914-row reference pool the floor is 0.6 points and the
    configured 2 points governs.
    """
    if n_rows <= 0 or runs <= 0:
        return 0.0
    return 300.0 * math.sqrt(chance * (1.0 - chance) / (n_rows * runs))


def control_is_valid(
    control: float, chance: float, tolerance_pp: float, *, n_rows: int = 0, runs: int = 1
) -> bool:
    """A control above chance + tolerance means the procedure inflates figures (FR-004.31).
    Below-chance controls are the known negative bias of cross-validated majority voting. The
    tolerance is never below the control's own sampling noise (:func:`control_noise_pp`)."""
    allowed = max(tolerance_pp, control_noise_pp(chance, n_rows, runs))
    return control <= chance + allowed / 100.0


def decide_warning(
    figure: float, chance: float, control: float, margin_pp: float, valid: bool
) -> bool:
    """P-19 (S3-06): valid, and at least the margin above both chance and the control."""
    m = margin_pp / 100.0
    return bool(valid and figure >= chance + m and figure >= control + m)


@dataclass(frozen=True)
class ColumnScore:
    figure: float
    control: float
    valid: bool


def score_column(
    x: np.ndarray,
    y: np.ndarray,
    k: int,
    *,
    folds: int,
    seed: int,
    tolerance_pp: float,
    fold_index: np.ndarray,
    permuted: list[tuple[np.ndarray, np.ndarray]],
) -> ColumnScore:
    """Figure, control and validity for one column (the audit service's one entry point)."""
    figure = balanced_accuracy_cv(x, y, k, folds=folds, seed=seed, fold_index=fold_index)
    control = permuted_control(
        x, y, k, folds=folds, seed=seed, runs=len(permuted), permuted=permuted
    )
    valid = control_is_valid(control, 1.0 / k, tolerance_pp, n_rows=len(y), runs=len(permuted))
    return ColumnScore(figure, control, valid)
