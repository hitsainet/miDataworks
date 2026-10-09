"""Length profiles and the quantile-overlap statistic (FR-009.9, FR-009.10; T-44).

Pure functions over arrays: the database never reaches here.

``overlap(cal, ref)``: ``a`` is the share of calibration-negative lengths inside the reference's
5th-95th percentile range, ``b`` the share of reference lengths inside the calibration negatives'
range; the figure is ``min(a, b)``. Identical distributions give about 0.9 by construction (90% of
each lies inside its own 5-95 band), so the measured tolerance in ``DETECTOR_LENGTH_OVERLAP_MIN`` is
read against that ceiling, never against 1.0.

The service hands ``overlap`` each profile's 1,001 PER-MILLE quantile points rather than every row
length: points equally spaced in probability are a faithful sample of the distribution, so the
shares differ from the full-array shares by at most 0.1 percentage points (FTID 009 section 3; the
deviation from "computed on arrays" is recorded in the controls review).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

QUANTILES: dict[str, float] = {
    "p05": 0.05,
    "p25": 0.25,
    "p50": 0.50,
    "p75": 0.75,
    "p95": 0.95,
    "p99": 0.99,
}
HISTOGRAM_BINS = 30
PERMILLE = np.linspace(0.0, 1.0, 1001)
#: Bump when the length measure changes (characters as DuckDB ``length()`` counts them, and
#: whitespace-separated words). A new value gives new cached rows, never an edit.
MEASURE_VERSION = "dw.length/v1"


@dataclass(frozen=True)
class Profile:
    n: int
    quantiles: dict[str, float]
    histogram: dict[str, Any]
    permille: list[float] = field(default_factory=list)


def profile(lengths: Sequence[float] | np.ndarray) -> Profile:
    """Quantiles (linear), a 30-bin histogram over [0, p99], and the per-mille points."""
    arr = np.asarray(lengths, dtype=np.float64)
    n = int(arr.size)
    if n == 0:
        return Profile(0, dict.fromkeys(QUANTILES, 0.0), {"edges": [], "counts": []}, [])
    qs = np.quantile(arr, list(QUANTILES.values()), method="linear")
    quantiles = {k: round(float(v), 4) for k, v in zip(QUANTILES, qs, strict=True)}
    upper = max(float(quantiles["p99"]), 1.0)
    counts, edges = np.histogram(np.clip(arr, 0, upper), bins=HISTOGRAM_BINS, range=(0, upper))
    permille = np.quantile(arr, PERMILLE, method="linear")
    return Profile(
        n,
        quantiles,
        {"edges": [round(float(e), 4) for e in edges], "counts": [int(c) for c in counts]},
        [round(float(v), 4) for v in permille],
    )


@dataclass(frozen=True)
class OverlapResult:
    figure: float
    cal_in_ref: float
    ref_in_cal: float
    ref_range: tuple[float, float]
    cal_range: tuple[float, float]


def band(values: np.ndarray) -> tuple[float, float]:
    lo, hi = np.quantile(values, [0.05, 0.95], method="linear")
    return float(lo), float(hi)


def overlap(cal: Sequence[float] | np.ndarray, ref: Sequence[float] | np.ndarray) -> OverlapResult:
    """Quantile overlap of two length distributions; the smaller share is the figure (T-44)."""
    a_arr = np.asarray(cal, dtype=np.float64)
    b_arr = np.asarray(ref, dtype=np.float64)
    if a_arr.size == 0 or b_arr.size == 0:
        return OverlapResult(0.0, 0.0, 0.0, (0.0, 0.0), (0.0, 0.0))
    ref_lo, ref_hi = band(b_arr)
    cal_lo, cal_hi = band(a_arr)
    a = float(np.mean((a_arr >= ref_lo) & (a_arr <= ref_hi)))
    b = float(np.mean((b_arr >= cal_lo) & (b_arr <= cal_hi)))
    return OverlapResult(
        figure=round(min(a, b), 4),
        cal_in_ref=round(a, 4),
        ref_in_cal=round(b, 4),
        ref_range=(round(ref_lo, 4), round(ref_hi, 4)),
        cal_range=(round(cal_lo, 4), round(cal_hi, 4)),
    )


def finest_fpr(n: int) -> float | None:
    """The finest false positive rate n calibration negatives afford: 1 / n (FR-009.11)."""
    return None if n <= 0 else 1.0 / n
