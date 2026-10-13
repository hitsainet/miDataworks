"""Pure diversity figures, intervals, the "falls" rule and the two controls (FTID 007 section 7.4).

PURE: the standard library and numpy only (``test_generation_pure_imports.py``).

- ``distinct_n``: distinct n-grams over all n-grams of a sample, with the ``lex-v1`` tokeniser
  (lowercase, Unicode-aware split on ``\\W+``). The tokeniser's version is part of a report's
  method identity, so a tokeniser change makes reports incomparable rather than silently
  different.
- ``centroid_spread`` (T-36): L2-normalise each embedding, centroid = their mean, spread = mean of
  ``1 - cos(e_i, centroid)``.
- ``cluster_coverage``: share of the reference's clusters a sample occupies, and the largest
  cluster's share of the sample.
- ``bootstrap_ci``: 95% percentile interval over ``B`` half-size subsamples without replacement,
  seeded; both sides of a comparison use one subsample size (see its docstring for why).
- ``falls`` (P-17): the WHOLE interval below the reference's ("higher is more diverse"), or for
  the largest-cluster share the whole interval ABOVE (more concentrated). A point estimate never
  decides it.
- Controls: ``collapse_sample`` (negative: half the rows replaced by copies from the largest
  cluster — must "fall"); ``split_halves`` (positive: two halves of the reference — must not).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np

TOKENISER = "lex-v1"
_SPLIT = re.compile(r"\W+", re.UNICODE)

Direction = Literal["higher_is_diverse", "lower_is_diverse"]


def tokens(text: str) -> list[str]:
    """``lex-v1``: lowercase, split on non-word runs, empties dropped."""
    return [t for t in _SPLIT.split(str(text).lower()) if t]


def ngrams(toks: Sequence[str], n: int) -> list[tuple[str, ...]]:
    return [tuple(toks[i : i + n]) for i in range(len(toks) - n + 1)]


def distinct_n(texts: Sequence[str], n: int) -> float | None:
    """Distinct n-grams / all n-grams across ``texts``; None when there is no n-gram at all."""
    seen: set[tuple[str, ...]] = set()
    total = 0
    for text in texts:
        grams = ngrams(tokens(text), n)
        total += len(grams)
        seen.update(grams)
    return (len(seen) / total) if total else None


def centroid_spread(embeddings: np.ndarray) -> float | None:
    """Mean cosine distance to the centroid of L2-normalised rows (T-36)."""
    matrix = np.asarray(embeddings, dtype=np.float64)
    if matrix.ndim != 2 or matrix.shape[0] == 0:
        return None
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    unit = matrix / norms
    centroid = unit.mean(axis=0)
    c_norm = float(np.linalg.norm(centroid))
    if c_norm == 0.0:
        return 1.0
    cos = unit @ (centroid / c_norm)
    return float(np.mean(1.0 - cos))


def cluster_coverage(
    assignments: Sequence[int] | np.ndarray, n_reference_clusters: int
) -> tuple[float, float]:
    """(share of reference clusters occupied, largest cluster's share of the sample)."""
    if not len(assignments) or n_reference_clusters <= 0:
        return 0.0, 0.0
    labels = np.asarray(assignments, dtype=np.int64)
    counts = np.bincount(labels)
    occupied = len(set(labels.tolist()))
    return occupied / float(n_reference_clusters), float(counts.max()) / float(len(labels))


@dataclass(frozen=True)
class Interval:
    value: float | None
    lo: float | None
    hi: float | None

    def as_dict(self) -> dict[str, float | None]:
        return {"value": self.value, "lo": self.lo, "hi": self.hi}


def bootstrap_ci(
    statistic: Callable[[np.ndarray], float],
    n_rows: int,
    rng: np.random.Generator,
    resamples: int,
    size: int | None = None,
) -> Interval:
    """A 95% percentile interval of ``statistic`` over ``resamples`` half-size SUBSAMPLES drawn
    without replacement (``size`` rows each; default half the rows).

    Why subsamples, not the classic with-replacement bootstrap: distinct-n and cluster coverage
    count DISTINCT things, and a with-replacement resample duplicates rows, which lowers both by
    construction (measured: a 50-row corpus's distinct-1 of 0.518 got the interval [0.364, 0.443],
    entirely below its own point estimate). A subsample of a fixed size has no duplicates; both
    sides of a comparison use the SAME size, so the two intervals describe the same quantity. The
    value reported is the median subsample statistic, so it always lies inside its interval.
    """
    if n_rows <= 1:
        return Interval(None, None, None)
    m = int(size) if size is not None else max(1, n_rows // 2)
    m = max(1, min(m, n_rows))
    values = np.empty(int(resamples), dtype=np.float64)
    for i in range(int(resamples)):
        values[i] = statistic(rng.permutation(n_rows)[:m])
    values = values[np.isfinite(values)]
    if values.size == 0:
        return Interval(None, None, None)
    lo, mid, hi = np.percentile(values, [2.5, 50.0, 97.5])
    return Interval(float(mid), float(lo), float(hi))


def distinct_ci(
    texts: Sequence[str],
    n: int,
    rng: np.random.Generator,
    resamples: int,
    size: int | None = None,
) -> Interval:
    """distinct-n with its interval. Each n-gram is mapped to an integer once; a resample's
    distinct count is ``count_nonzero(bincount(ids of the chosen rows))`` — vectorised."""
    vocab: dict[tuple[str, ...], int] = {}
    flat: list[int] = []
    lengths: list[int] = []
    for text in texts:
        grams = ngrams(tokens(text), n)
        lengths.append(len(grams))
        for gram in grams:
            flat.append(vocab.setdefault(gram, len(vocab)))
    ids = np.asarray(flat, dtype=np.int64)
    sizes = np.asarray(lengths, dtype=np.int64)
    owner = np.repeat(np.arange(len(texts)), sizes)
    v = len(vocab)

    def stat(idx: np.ndarray) -> float:
        counts = np.bincount(idx, minlength=len(texts))
        total = int((counts * sizes).sum())
        if total == 0:
            return float("nan")
        chosen = ids[counts[owner] > 0]
        return float(np.count_nonzero(np.bincount(chosen, minlength=v))) / float(total)

    return bootstrap_ci(stat, len(texts), rng, resamples, size)


def spread_ci(
    embeddings: np.ndarray, rng: np.random.Generator, resamples: int, size: int | None = None
) -> Interval:
    matrix = np.asarray(embeddings, dtype=np.float64)

    def stat(idx: np.ndarray) -> float:
        value = centroid_spread(matrix[idx])
        return float("nan") if value is None else value

    return bootstrap_ci(stat, matrix.shape[0], rng, resamples, size)


def coverage_ci(
    assignments: Sequence[int] | np.ndarray,
    n_clusters: int,
    rng: np.random.Generator,
    resamples: int,
    size: int | None = None,
) -> tuple[Interval, Interval]:
    labels = np.asarray(assignments, dtype=np.int64)
    cov = bootstrap_ci(
        lambda idx: cluster_coverage(labels[idx], n_clusters)[0], len(labels), rng, resamples, size
    )
    share = bootstrap_ci(
        lambda idx: cluster_coverage(labels[idx], n_clusters)[1], len(labels), rng, resamples, size
    )
    return cov, share


def falls(version: Interval, reference: Interval, direction: Direction) -> bool:
    """True only when the WHOLE version interval lies on the less-diverse side (P-17)."""
    if None in (version.lo, version.hi, reference.lo, reference.hi):
        return False
    assert version.hi is not None and version.lo is not None
    assert reference.lo is not None and reference.hi is not None
    if direction == "higher_is_diverse":
        return version.hi < reference.lo
    return version.lo > reference.hi


def collapse_sample(
    n_rows: int, assignments: Sequence[int] | np.ndarray, rng: np.random.Generator
) -> np.ndarray:
    """Row indices of the negative control: half the rows replaced by copies drawn from the
    largest cluster. Copies of real rows (not duplication of one row), so spread moves too."""
    if n_rows == 0:
        return np.arange(0)
    labels = np.asarray(assignments, dtype=np.int64)
    largest = int(np.bincount(labels).argmax())
    members = np.flatnonzero(labels == largest)
    idx = np.arange(n_rows)
    replace = rng.choice(n_rows, size=n_rows // 2, replace=False)
    idx[replace] = rng.choice(members, size=replace.size, replace=True)
    return idx


def collapse_toward(vectors: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Row indices of the negative control IN EMBEDDING SPACE: half the rows replaced by copies of
    one seeded anchor's nearest neighbours (a tenth of the rows). The lexical collapse cannot test
    spread — its clusters are not tight in embedding space, and copies of arbitrary rows leave a
    centroid distance unchanged in expectation (measured: spread did not fall on such a sample)."""
    matrix = np.asarray(vectors, dtype=np.float64)
    n = matrix.shape[0]
    if n == 0:
        return np.arange(0)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    unit = matrix / norms
    anchor = int(rng.integers(0, n))
    nearest = np.argsort(-(unit @ unit[anchor]))[: max(2, n // 10)]
    idx = np.arange(n)
    replace = rng.choice(n, size=n // 2, replace=False)
    idx[replace] = rng.choice(nearest, size=replace.size, replace=True)
    return idx


def split_halves(n_rows: int, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """Two disjoint random halves of the reference (the positive control)."""
    order = rng.permutation(n_rows)
    half = n_rows // 2
    return np.sort(order[:half]), np.sort(order[half : 2 * half])
