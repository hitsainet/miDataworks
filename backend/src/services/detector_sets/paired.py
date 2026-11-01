# Origin: miDataworks prototype scripts/paired_probe.py @ eedbf288 (pairs_of, paired_accuracy,
# cluster_ci). Mode: adapt (docs/REUSE.md). Kept: ties count one half; the confidence interval
# resamples GROUPS, not pairs, 2,000 times, 2.5th and 97.5th percentiles; miStudio's own arrays are
# accepted within 5e-4 of the reported AUROC (paired_probe.py:108). Changed: no pandas, labels and
# groups arrive as sequences; the reproduction rule also accepts miLLM scores whose AUROC lies inside
# miStudio's reported 95% interval (T-47, FR-009.77); the bootstrap generator is passed in, seeded.
"""Paired score and its group bootstrap (FR-009.37, FR-009.38; FTDD 009 section 7.2).

The paired score is the share of cross-label pairs inside a group where the positive row scores
higher, ties counting one half. A score is computed ONLY from per-row scores that reproduce
miStudio's reported AUROC for that set (:func:`accept_source`); anything else is refused with both
figures, never estimated.
"""

from __future__ import annotations

from collections.abc import Hashable, Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np
from sklearn.metrics import roc_auc_score

BOOTSTRAP = 2000
SEED = 20261005
MISTUDIO_ARRAY_TOLERANCE = 5e-4

Source = Literal["millm", "mistudio"]


def pairs_of(
    labels: Sequence[bool], groups: Sequence[Hashable]
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Row indices (positive, negative) of every cross-label pair within a group, and each pair's
    group code (for the group bootstrap). ``labels`` are True for positive rows."""
    by_group: dict[Hashable, list[int]] = {}
    for i, g in enumerate(groups):
        if g is None:
            continue
        by_group.setdefault(g, []).append(i)
    pos: list[int] = []
    neg: list[int] = []
    code: list[int] = []
    for c, (_, rows) in enumerate(sorted(by_group.items(), key=lambda kv: str(kv[0]))):
        positives = [i for i in rows if labels[i]]
        negatives = [i for i in rows if not labels[i]]
        for a in positives:
            for b in negatives:
                pos.append(a)
                neg.append(b)
                code.append(c)
    return np.array(pos, dtype=np.int64), np.array(neg, dtype=np.int64), np.array(code)


def paired_wins(
    scores: Sequence[float] | np.ndarray, pos: np.ndarray, neg: np.ndarray
) -> np.ndarray:
    """Per pair: 1 if the positive row scores higher, 0.5 on a tie, 0 otherwise."""
    s = np.asarray(scores, dtype=np.float64)
    a, b = s[pos], s[neg]
    out: np.ndarray = np.where(a > b, 1.0, np.where(a == b, 0.5, 0.0))
    return out


def group_ci(
    wins: np.ndarray,
    group: np.ndarray,
    rng: np.random.Generator,
    resamples: int = BOOTSTRAP,
) -> tuple[float, float]:
    """95% interval resampling GROUPS, since pairs sharing a group are not independent."""
    codes = np.unique(group)
    by = {c: wins[group == c] for c in codes}
    stats = []
    for _ in range(resamples):
        pick = rng.choice(codes, len(codes))
        stats.append(np.concatenate([by[c] for c in pick]).mean())
    lo, hi = np.quantile(stats, [0.025, 0.975])
    return round(float(lo), 4), round(float(hi), 4)


@dataclass(frozen=True)
class PairedScore:
    paired: float
    ci: tuple[float, float]
    pairs: int
    groups: int


def paired_score(
    scores: Sequence[float] | np.ndarray,
    labels: Sequence[bool],
    groups: Sequence[Hashable],
    *,
    seed: int = SEED,
) -> PairedScore | None:
    """The paired score with its group interval, or None when no cross-label pair exists."""
    pos, neg, code = pairs_of(labels, groups)
    if pos.size == 0:
        return None
    wins = paired_wins(scores, pos, neg)
    ci = group_ci(wins, code, np.random.default_rng(seed))
    return PairedScore(round(float(wins.mean()), 4), ci, int(pos.size), int(np.unique(code).size))


@dataclass(frozen=True)
class Acceptance:
    accepted: bool
    auroc: float
    reason: str


def accept_source(
    source: Source,
    scores: Sequence[float] | np.ndarray,
    labels: Sequence[bool],
    *,
    reported_auroc: float,
    reported_ci: tuple[float, float] | None,
) -> Acceptance:
    """Do these per-row scores reproduce miStudio's reported AUROC for the set (FR-009.38)?

    - ``millm``: the AUROC lies inside miStudio's reported 95% interval (FR-009.77, T-47);
    - ``mistudio``: the AUROC is within 5e-4 of the reported value (the prototype's rule).
    """
    y = np.asarray([1 if v else 0 for v in labels])
    s = np.asarray(scores, dtype=np.float64)
    if s.size != y.size:
        return Acceptance(False, float("nan"), f"{s.size} scores for {y.size} rows")
    if y.min(initial=1) == y.max(initial=0):
        return Acceptance(False, float("nan"), "one class only; no AUROC")
    auroc = float(roc_auc_score(y, s))
    if source == "mistudio":
        ok = abs(auroc - reported_auroc) <= MISTUDIO_ARRAY_TOLERANCE
        rule = f"within {MISTUDIO_ARRAY_TOLERANCE} of miStudio's {reported_auroc:.4f}"
    else:
        if reported_ci is None:
            return Acceptance(False, auroc, "miStudio reported no interval to reproduce against")
        ok = reported_ci[0] <= auroc <= reported_ci[1]
        rule = f"inside miStudio's interval [{reported_ci[0]:.4f}, {reported_ci[1]:.4f}]"
    reason = f"AUROC {auroc:.4f} {'is' if ok else 'is not'} {rule}" + (
        "" if ok else "; the scores do not reproduce the evaluation, so no paired score"
    )
    return Acceptance(ok, round(auroc, 6), reason)
