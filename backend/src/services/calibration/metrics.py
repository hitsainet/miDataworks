"""Pure calibration metrics (FR-006.7, FR-006.10 – FR-006.13, FR-006.41; FTID 006 sections 7.1–7.5).

Guarantees: no input/output; every figure comes with its sample size; a figure that cannot be
computed is ``None`` with a reason at the caller, never a zero. Re-implemented (not copied) from
the prototype: ``scripts/validate_judge.py`` (``bootstrap``, ``metrics``) and
``scripts/paired_probe.py`` (``pairs_of``, ``paired_accuracy``, ``cluster_ci``). The draw sequence
— a fresh ``default_rng(seed)``, ``integers(0, n, n)`` per resample — and ``np.quantile``'s default
method are what make ``tests/unit/calibration/test_prototype_golden.py`` exact.

Refuses: mismatched array lengths and empty inputs (``ValueError`` with a reason).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy.stats import rankdata
from sklearn.metrics import cohen_kappa_score, roc_auc_score

from .constants import (
    BOOTSTRAP_RESAMPLES,
    RELIABILITY_BINS,
    RELIABILITY_MIN_ROWS,
    SEED_AUROC,
    SEED_PAIRS,
)

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]


@dataclass(frozen=True)
class Interval:
    """A point estimate with its percentile bootstrap 95% interval and sample facts."""

    value: float
    ci_low: float
    ci_high: float
    n: int
    resamples: int
    dropped: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "ci_low": self.ci_low,
            "ci_high": self.ci_high,
            "n": self.n,
            "resamples": self.resamples,
            "dropped": self.dropped,
        }


def _check_same_length(*arrays: Sequence[Any] | NDArray[Any]) -> int:
    lengths = {len(a) for a in arrays}
    if len(lengths) != 1:
        raise ValueError(f"arrays have different lengths: {sorted(lengths)}")
    n = lengths.pop()
    if n == 0:
        raise ValueError("no rows")
    return n


def both_classes(y: NDArray[Any]) -> bool:
    total = int(np.sum(y))
    return 0 < total < len(y)


def auroc(y: NDArray[Any], p: NDArray[Any]) -> float:
    """AUROC with scikit-learn's arithmetic (point estimates match the prototype exactly)."""
    _check_same_length(y, p)
    if not both_classes(y):
        raise ValueError("AUROC needs both classes")
    return float(roc_auc_score(y, p))


def auroc_fast(y: NDArray[Any], s: NDArray[Any]) -> float:
    """Mann–Whitney AUROC with average ranks; equal to ``roc_auc_score`` to 1e-12 (ties too).

    Used only by the ceiling and the permutation null, where tens of thousands are computed.
    """
    _check_same_length(y, s)
    yy = np.asarray(y).astype(bool)
    n1 = int(yy.sum())
    n0 = len(yy) - n1
    if n1 == 0 or n0 == 0:
        raise ValueError("AUROC needs both classes")
    r = rankdata(s)
    return float((r[yy].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def bootstrap_auroc_ci(
    y: NDArray[Any],
    p: NDArray[Any],
    *,
    seed: int = SEED_AUROC,
    resamples: int = BOOTSTRAP_RESAMPLES,
) -> Interval:
    """AUROC and its percentile 95% interval (FR-006.7).

    A resample with one class only is DROPPED and counted (the prototype appended NaN, which is
    identical whenever none occur). A fresh generator per call, one ``integers(0, n, n)`` draw per
    resample.
    """
    y = np.asarray(y)
    p = np.asarray(p, dtype=np.float64)
    n = _check_same_length(y, p)
    point = auroc(y, p)
    rng = np.random.default_rng(seed)
    stats: list[float] = []
    dropped = 0
    for _ in range(resamples):
        idx = rng.integers(0, n, n)
        yy = y[idx]
        if 0 < yy.sum() < n:
            stats.append(float(roc_auc_score(yy, p[idx])))
        else:
            dropped += 1
    if not stats:
        raise ValueError("every bootstrap resample lacked a class")
    lo, hi = np.quantile(stats, [0.025, 0.975])
    return Interval(point, float(lo), float(hi), n, resamples, dropped)


# --------------------------------------------------------------------------------------------
# Same-group pairs (FR-006.10)
# --------------------------------------------------------------------------------------------


def cross_label_pairs(
    group: Sequence[Any],
    label: Sequence[int | None],
    strata: Sequence[str | None] | None = None,
) -> tuple[IntArray, IntArray, IntArray]:
    """Every (positive, negative) pair of rows within one group that agree on every stratum.

    Returns row indices ``(positive, negative)`` and each pair's group code. Groups are coded in
    ascending order of their key, as the prototype's ``groupby`` does; rows with no group or no
    label take part in no pair.
    """
    n = _check_same_length(group, label)
    if strata is not None:
        _check_same_length(group, strata)
    members: dict[Any, list[int]] = {}
    for i in range(n):
        if group[i] is None or label[i] is None:
            continue
        members.setdefault(group[i], []).append(i)
    pos: list[int] = []
    neg: list[int] = []
    codes: list[int] = []
    for code, key in enumerate(sorted(members)):
        rows = members[key]
        positives = [i for i in rows if label[i] == 1]
        negatives = [i for i in rows if label[i] == 0]
        for a in positives:
            for b in negatives:
                if strata is not None and strata[a] != strata[b]:
                    continue
                pos.append(a)
                neg.append(b)
                codes.append(code)
    return (
        np.array(pos, dtype=np.int64),
        np.array(neg, dtype=np.int64),
        np.array(codes, dtype=np.int64),
    )


def paired_accuracy(scores: NDArray[Any], pos: IntArray, neg: IntArray) -> FloatArray:
    """Per pair: 1 if the positive row scores higher, 0.5 on a tie, 0 otherwise."""
    s = np.asarray(scores, dtype=np.float64)
    a, b = s[pos], s[neg]
    return np.where(a > b, 1.0, np.where(a == b, 0.5, 0.0))


def cluster_bootstrap_ci(
    wins: FloatArray,
    group_code: IntArray,
    *,
    seed: int = SEED_PAIRS,
    resamples: int = BOOTSTRAP_RESAMPLES,
) -> Interval:
    """Mean of ``wins`` with a 95% interval that resamples GROUPS, not pairs (``cluster_ci``):
    pairs that share a group are not independent."""
    _check_same_length(wins, group_code)
    codes = np.unique(group_code)
    by = {int(c): wins[group_code == c] for c in codes}
    rng = np.random.default_rng(seed)
    stats: list[float] = []
    for _ in range(resamples):
        pick = rng.choice(codes, len(codes))
        stats.append(float(np.concatenate([by[int(c)] for c in pick]).mean()))
    lo, hi = np.quantile(stats, [0.025, 0.975])
    return Interval(float(np.mean(wins)), float(lo), float(hi), len(wins), resamples, 0)


@dataclass(frozen=True)
class ReferenceWins:
    """Each labeled non-reference row against its group's reference row (FR-006.41)."""

    positive: FloatArray
    positive_group: IntArray
    negative: FloatArray
    negative_group: IntArray


def reference_pairs(
    group: Sequence[Any],
    is_reference: Sequence[bool],
    label: Sequence[int | None],
    scores: NDArray[Any],
) -> ReferenceWins:
    """Win arrays for the reference diagnostic and its negative-class control.

    A row wins when it scores STRICTLY above its group's reference (the prototype's
    ``validate_judge.metrics``, which produced 0.952 / 0.873). The first reference row of a group,
    in row order, is its reference; groups without one contribute nothing.
    """
    n = _check_same_length(group, is_reference, label, scores)
    s = np.asarray(scores, dtype=np.float64)
    reference: dict[Any, int] = {}
    for i in range(n):
        if is_reference[i] and group[i] is not None and group[i] not in reference:
            reference[group[i]] = i
    codes = {key: code for code, key in enumerate(sorted(reference))}
    pos: list[float] = []
    pos_g: list[int] = []
    neg: list[float] = []
    neg_g: list[int] = []
    for i in range(n):
        if is_reference[i] or label[i] is None or group[i] not in reference:
            continue
        win = 1.0 if s[i] > s[reference[group[i]]] else 0.0
        if label[i] == 1:
            pos.append(win)
            pos_g.append(codes[group[i]])
        elif label[i] == 0:
            neg.append(win)
            neg_g.append(codes[group[i]])
    return ReferenceWins(
        np.array(pos, dtype=np.float64),
        np.array(pos_g, dtype=np.int64),
        np.array(neg, dtype=np.float64),
        np.array(neg_g, dtype=np.int64),
    )


# --------------------------------------------------------------------------------------------
# Reliability, bands, kappa, multi-class (FR-006.11 – FR-006.13)
# --------------------------------------------------------------------------------------------


def reliability_edges(bins: int = RELIABILITY_BINS) -> FloatArray:
    return np.linspace(0, 1, bins + 1)


def bin_index(p: NDArray[Any], bins: int = RELIABILITY_BINS) -> IntArray:
    """Right-closed bins ``(a, b]`` with 0 in the first: ``pd.cut(..., include_lowest=True)``."""
    edges = reliability_edges(bins)
    idx = np.searchsorted(edges, np.asarray(p, dtype=np.float64), side="left") - 1
    return np.clip(idx, 0, bins - 1).astype(np.int64)


def reliability_bins(
    y: NDArray[Any],
    p: NDArray[Any],
    *,
    bins: int = RELIABILITY_BINS,
    min_rows: int = RELIABILITY_MIN_ROWS,
) -> list[dict[str, Any]]:
    """Per bin: edges, row count, the share people labeled positive, and whether it is sparse."""
    _check_same_length(y, p)
    yy = np.asarray(y, dtype=np.float64)
    idx = bin_index(p, bins)
    edges = reliability_edges(bins)
    out: list[dict[str, Any]] = []
    for b in range(bins):
        mask = idx == b
        count = int(mask.sum())
        out.append(
            {
                "lo": float(edges[b]),
                "hi": float(edges[b + 1]),
                "n": count,
                "positive_fraction": float(yy[mask].mean()) if count else None,
                "sparse": count < min_rows,
            }
        )
    return out


def _band(p: FloatArray, positive: float, negative: float) -> dict[str, float | int]:
    n = len(p)
    above = int((p >= positive).sum())
    below = int((p <= negative).sum())
    return {
        "n": n,
        "at_or_above": above / n if n else 0.0,
        "at_or_below": below / n if n else 0.0,
        "excluded": (n - above - below) / n if n else 0.0,
    }


def band_shares(
    y: NDArray[Any],
    p: NDArray[Any],
    *,
    threshold_positive: float,
    threshold_negative: float,
) -> dict[str, Any]:
    """Shares at or above the upper threshold, at or below the lower one, and between, using the
    label run's thresholds inclusively at both ends (FR-005.18), overall and per human class."""
    _check_same_length(y, p)
    if threshold_negative >= threshold_positive:
        raise ValueError("the negative threshold must be below the positive one")
    pp = np.asarray(p, dtype=np.float64)
    yy = np.asarray(y)
    return {
        "threshold_positive": threshold_positive,
        "threshold_negative": threshold_negative,
        "overall": _band(pp, threshold_positive, threshold_negative),
        "by_class": {
            "positive": _band(pp[yy == 1], threshold_positive, threshold_negative),
            "negative": _band(pp[yy == 0], threshold_positive, threshold_negative),
        },
    }


def kappa_on_kept(
    labeler_label: Sequence[str | None], human_label: Sequence[str | None]
) -> dict[str, Any] | None:
    """Cohen's kappa on rows the band kept (a labeler label exists) and people labeled (T-28)."""
    _check_same_length(labeler_label, human_label)
    pairs = [(a, b) for a, b in zip(labeler_label, human_label, strict=True) if a is not None and b]
    if len(pairs) < 2:
        return None
    a, b = zip(*pairs, strict=True)
    if len(set(a) | set(b)) < 2:
        return None
    return {"value": float(cohen_kappa_score(list(a), list(b))), "n_kept": len(pairs)}


def macro_auroc_ovr(
    human_label: Sequence[str], distributions: Sequence[dict[str, float]], label_set: Sequence[str]
) -> dict[str, Any]:
    """One-versus-rest AUROC per class and their macro average (T-28)."""
    _check_same_length(human_label, distributions)
    by_class: dict[str, float | None] = {}
    for label in label_set:
        y = np.array([1 if h == label else 0 for h in human_label])
        s = np.array([float(d.get(label, 0.0)) for d in distributions])
        by_class[label] = float(roc_auc_score(y, s)) if both_classes(y) else None
    defined = [v for v in by_class.values() if v is not None]
    return {"macro": float(np.mean(defined)) if defined else None, "by_class": by_class}
