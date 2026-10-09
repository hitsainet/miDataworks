"""The held-out human rater ceiling and the like-for-like comparison (FR-006.4, FR-006.8, FR-006.9).

Guarantees:
- **There is no fixed-position hold-out.** :func:`held_out_draws` takes no position argument and is
  the only code path that holds a rating out: each draw picks one rating per row AT RANDOM. On
  Humicroedit every ``grades`` string is sorted, so position 1 or 2 "reproduces" AUROC 1.000 and
  positions 0 and 3 are inflated too (FTDD 006 section 4.4); a test asserts the signature.
- The held-out rating itself is the score; the mean of the remaining ratings is labeled by the
  set's numeric rule (excluded middle). The comparison scores the labeler on EACH draw's kept rows
  against THAT draw's consensus labels.
- The interval is a row bootstrap: one row-index draw per resample, shared by every draw and by
  both scorers; each draw's AUROC on its kept resampled rows; the average across draws.

Refuses (``ValueError``): unknown ratings formats, non-integer ratings, mismatched lengths.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

from .constants import (
    BOOTSTRAP_RESAMPLES,
    MIN_RATINGS_FOR_CEILING,
    RATER_DRAWS,
    SEED_CEILING_BOOTSTRAP,
    SEED_CEILING_DRAWS,
)
from .metrics import Interval, auroc_fast

RATINGS_FORMATS: tuple[str, ...] = ("digit_string", "list")


def parse_ratings(value: Any, fmt: str) -> list[int] | None:
    """One row's ratings in stored order; ``None`` for a missing value.

    An EMPTY value is missing too: an empty digit string (or an empty list) holds no rating.
    Humicroedit's prepared original rows carry ``grades == ""`` (the prototype's
    ``scripts/prepare.py``), and refusing it made the documented FTDD 006 section 4.3 mapping
    unable to import the data it was written for (live acceptance, 2026-10-07).
    """
    if value is None or (isinstance(value, str | list | tuple) and len(value) == 0):
        return None
    if fmt == "digit_string":
        if not isinstance(value, str) or not value.isdigit():
            raise ValueError(f"a digit_string rating must be a string of digits, got {value!r}")
        return [int(ch) for ch in value]
    if fmt == "list":
        if not isinstance(value, list | tuple) or not all(
            isinstance(v, int | np.integer) and not isinstance(v, bool) for v in value
        ):
            raise ValueError(f"a list rating must be a list of integers, got {value!r}")
        return [int(v) for v in value]
    raise ValueError(f"unknown ratings format {fmt!r}; use one of {list(RATINGS_FORMATS)}")


def ratings_sorted(rows: Sequence[Sequence[int] | None]) -> bool:
    """True when every row with two or more ratings is non-increasing, or every such row is
    non-decreasing: the positions are then ranks, not rater identities (FR-006.4)."""
    multi = [list(r) for r in rows if r is not None and len(r) >= 2]
    if not multi:
        return False
    non_increasing = all(all(a >= b for a, b in zip(r, r[1:], strict=False)) for r in multi)
    non_decreasing = all(all(a <= b for a, b in zip(r, r[1:], strict=False)) for r in multi)
    return non_increasing or non_decreasing


def ratings_matrix(
    rows: Sequence[Sequence[int] | None],
) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
    """Pad to a matrix with ``-1``; returns ``(R, counts)``."""
    width = max((len(r) for r in rows if r is not None), default=0)
    matrix = np.full((len(rows), max(width, 1)), -1, dtype=np.int64)
    counts = np.zeros(len(rows), dtype=np.int64)
    for i, r in enumerate(rows):
        if r:
            matrix[i, : len(r)] = r
            counts[i] = len(r)
    return matrix, counts


@dataclass(frozen=True)
class Draw:
    """One random hold-out: the rows it kept, their held rating and consensus label."""

    kept: NDArray[np.int64]  # row positions into the input rows
    held: NDArray[np.float64]
    consensus: NDArray[np.int64]
    positions: NDArray[np.int64]  # the held position of every eligible row (for the check)


def held_out_draws(
    ratings: NDArray[np.int64],
    counts: NDArray[np.int64],
    *,
    positive_at_or_above: float,
    negative_at_or_below: float,
    draws: int = RATER_DRAWS,
    seed: int = SEED_CEILING_DRAWS,
) -> list[Draw]:
    """``draws`` random hold-outs over the rows with at least three ratings.

    Per draw and row: ``k = rng.integers(0, c)``; the held rating is ``R[i, k]``; the rest's mean
    is labeled positive at or above, negative at or below, and the row leaves the draw in between.
    """
    if negative_at_or_below >= positive_at_or_above:
        raise ValueError("the negative cut point must be below the positive one")
    eligible = np.flatnonzero(counts >= MIN_RATINGS_FOR_CEILING)
    R = ratings[eligible]
    c = counts[eligible]
    total = np.where(R >= 0, R, 0).sum(axis=1)
    rng = np.random.default_rng(seed)
    out: list[Draw] = []
    rows = np.arange(len(eligible))
    for _ in range(draws):
        k = rng.integers(0, c) if len(c) else np.zeros(0, dtype=np.int64)
        held = R[rows, k].astype(np.float64)
        rest = (total - held) / (c - 1)
        positive = rest >= positive_at_or_above
        negative = rest <= negative_at_or_below
        keep = positive | negative
        out.append(
            Draw(
                kept=eligible[keep].astype(np.int64),
                held=held[keep],
                consensus=positive[keep].astype(np.int64),
                positions=np.asarray(k, dtype=np.int64),
            )
        )
    return out


class _SortedScorer:
    """A scorer's order and tie groups over one draw's rows, computed once per draw."""

    def __init__(self, y: NDArray[Any], s: NDArray[Any]) -> None:
        self.order = np.argsort(np.asarray(s, dtype=np.float64), kind="stable")
        s_sorted = np.asarray(s, dtype=np.float64)[self.order]
        _, self.group = np.unique(s_sorted, return_inverse=True)
        self.positive = np.asarray(y).astype(bool)[self.order]
        self.n_groups = int(self.group.max()) + 1 if len(self.group) else 0

    def auroc(self, w: NDArray[Any]) -> float | None:
        ww = np.asarray(w, dtype=np.float64)[self.order]
        gp = np.bincount(self.group, weights=ww * self.positive, minlength=self.n_groups)
        gn = np.bincount(self.group, weights=ww * ~self.positive, minlength=self.n_groups)
        total_p, total_n = gp.sum(), gn.sum()
        if total_p == 0 or total_n == 0:
            return None
        below = np.cumsum(gn) - gn
        return float((gp * (below + 0.5 * gn)).sum() / (total_p * total_n))


def weighted_auroc(y: NDArray[Any], s: NDArray[Any], w: NDArray[Any]) -> float | None:
    """AUROC of a multiset: row ``i`` counted ``w[i]`` times (Mann–Whitney, ties at one half).

    Equal to ``roc_auc_score`` on the expanded resample; ``None`` when a class has no weight.
    """
    return _SortedScorer(y, s).auroc(w)


@dataclass(frozen=True)
class CeilingResult:
    ceiling: Interval
    comparison: Interval
    draw_values: list[float]
    comparison_values: list[float]
    n_mean: float
    draws: int


def ceiling_and_comparison(
    draws: Sequence[Draw],
    labeler_scores: NDArray[Any],
    *,
    n_rows: int,
    seed: int = SEED_CEILING_BOOTSTRAP,
    resamples: int = BOOTSTRAP_RESAMPLES,
) -> CeilingResult | None:
    """The ceiling (held rating's AUROC) and the comparison (labeler's AUROC on the same rows and
    labels), each the mean over draws, with a shared row-bootstrap interval. ``None`` when no
    draw has both classes."""
    scores = np.asarray(labeler_scores, dtype=np.float64)
    usable = [d for d in draws if 0 < int(d.consensus.sum()) < len(d.consensus)]
    if not usable:
        return None
    draw_values = [auroc_fast(d.consensus, d.held) for d in usable]
    comparison_values = [auroc_fast(d.consensus, scores[d.kept]) for d in usable]
    sorted_pairs = [
        (d, _SortedScorer(d.consensus, d.held), _SortedScorer(d.consensus, scores[d.kept]))
        for d in usable
    ]
    rng = np.random.default_rng(seed)
    ceil_stats: list[float] = []
    comp_stats: list[float] = []
    dropped = 0
    for _ in range(resamples):
        counts = np.bincount(rng.integers(0, n_rows, n_rows), minlength=n_rows)
        per_ceil: list[float] = []
        per_comp: list[float] = []
        for d, held_scorer, labeler_scorer in sorted_pairs:
            w = counts[d.kept]
            a = held_scorer.auroc(w)
            b = labeler_scorer.auroc(w)
            if a is not None and b is not None:
                per_ceil.append(a)
                per_comp.append(b)
        if not per_ceil:
            dropped += 1
            continue
        ceil_stats.append(float(np.mean(per_ceil)))
        comp_stats.append(float(np.mean(per_comp)))
    if not ceil_stats:
        return None
    n_mean = float(np.mean([len(d.kept) for d in usable]))
    c_lo, c_hi = np.quantile(ceil_stats, [0.025, 0.975])
    m_lo, m_hi = np.quantile(comp_stats, [0.025, 0.975])
    return CeilingResult(
        ceiling=Interval(
            float(np.mean(draw_values)), float(c_lo), float(c_hi), int(n_mean), resamples, dropped
        ),
        comparison=Interval(
            float(np.mean(comparison_values)),
            float(m_lo),
            float(m_hi),
            int(n_mean),
            resamples,
            dropped,
        ),
        draw_values=draw_values,
        comparison_values=comparison_values,
        n_mean=n_mean,
        draws=len(usable),
    )
