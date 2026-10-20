"""Stratified samples for audits and calibration-labeling queues (FR-006.26, FR-006.27; T-26;
FTID 006 section 7.9).

Guarantees: deterministic for a seed (candidates sorted by row key, strata in sorted order, one
generator); proportional allocation by largest remainder; at least one row from every non-empty
stratum when the size allows; exactly ``size`` rows (or every row when fewer exist). No I/O.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np


def allocate(counts: dict[str, int], size: int) -> dict[str, int]:
    """Rows per stratum: proportional, largest remainder, at least one each, total exactly size."""
    total = sum(counts.values())
    if size >= total:
        return dict(counts)
    strata = sorted(k for k, v in counts.items() if v > 0)
    if len(strata) > size:
        # More strata than rows: one each from the largest strata (ties by key).
        chosen = sorted(strata, key=lambda k: (-counts[k], k))[:size]
        return {k: (1 if k in chosen else 0) for k in counts}
    quota = {k: size * counts[k] / total for k in strata}
    alloc = {k: min(counts[k], max(1, math.floor(quota[k]))) for k in strata}
    remainder = {k: quota[k] - math.floor(quota[k]) for k in strata}
    while sum(alloc.values()) < size:
        room = [k for k in strata if alloc[k] < counts[k]]
        k = sorted(room, key=lambda s: (-remainder[s], s))[0]
        alloc[k] += 1
        remainder[k] -= 1.0
    while sum(alloc.values()) > size:
        spare = [k for k in strata if alloc[k] > 1]
        k = sorted(spare, key=lambda s: (remainder[s], s))[0]
        alloc[k] -= 1
        remainder[k] += 1.0
    return {k: alloc.get(k, 0) for k in counts}


def stratified_sample(
    rows: Sequence[tuple[str, str]], size: int, seed: int
) -> list[tuple[str, str]]:
    """``rows`` are ``(row_key, stratum)``; returns the sampled pairs sorted by row key."""
    if size < 1:
        raise ValueError("the sample size must be at least 1")
    by: dict[str, list[str]] = {}
    for key, stratum in sorted(set(rows)):
        by.setdefault(stratum, []).append(key)
    alloc = allocate({s: len(v) for s, v in by.items()}, size)
    rng = np.random.default_rng(seed)
    picked: list[tuple[str, str]] = []
    for stratum in sorted(by):
        k = alloc[stratum]
        if k == 0:
            continue
        keys = by[stratum]
        chosen = rng.choice(len(keys), size=k, replace=False)
        picked.extend((keys[int(i)], stratum) for i in sorted(chosen))
    return sorted(picked)
