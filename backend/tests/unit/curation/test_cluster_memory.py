"""Lexical cluster assignment without densifying the whole matrix (pre-existing defect, fixed
2026-10-19 in the 007 method version 3 review; record ``0xcc/reviews/007_largest_share_v3_2026-10-19.md``).

``assign_matrix`` and ``fit``'s per-row distances turned the TF-IDF matrix into a dense
rows x 32,768 float64 array: 1.3 GB at the diversity report's 5,000 rows (the benchmark peaked at
3.4 GB), 26 GB at ``DIVERSITY_SAMPLE_SIZE``'s ceiling. The values must not change: same labels,
distances within rounding (assignment) or bit for bit (``fit``'s distances).
"""

from __future__ import annotations

import random
import tracemalloc

import numpy as np
import pytest
from sklearn.feature_extraction.text import TfidfTransformer
from sklearn.preprocessing import normalize

from src.services.curation import cluster_service as cs

TOPICS = [[f"t{t}w{i}" for i in range(80)] for t in range(8)]


def sentences(n: int, seed: int) -> list[str]:
    rng = random.Random(seed)  # noqa: S311 - a test corpus
    words = [f"rare{i}" for i in range(5000)]
    return [
        " ".join([*(rng.choice(TOPICS[i % 8]) for _ in range(8)), *rng.sample(words, 6)])
        for i in range(n)
    ]


def dense_assign(texts: list[str], idf: np.ndarray, centroids: np.ndarray) -> np.ndarray:
    """The assignment as it was written before the fix (whole matrix dense)."""
    counts = cs.vectoriser().transform(texts)
    dense = normalize(counts.multiply(idf.reshape(1, -1)).tocsr()).toarray()
    d2 = (
        (dense * dense).sum(axis=1, keepdims=True)
        - 2 * dense @ centroids.T
        + (centroids * centroids).sum(axis=1)
    )
    return np.argmin(d2, axis=1)


def test_sparse_assignment_gives_the_dense_labels() -> None:
    reference, other = sentences(300, 1), sentences(500, 2)
    for seed in range(3):
        fitted = cs.fit(reference, 12, seed)
        labels, distances = cs.assign_matrix(other, fitted.idf, fitted.centroids)
        assert np.array_equal(labels, dense_assign(other, fitted.idf, fitted.centroids))
        assert np.all(distances >= 0.0)


def test_fit_distances_are_unchanged_across_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cs, "DISTANCE_CHUNK", 64)  # 300 rows: five chunks, the last partial
    texts = sentences(300, 3)
    fitted = cs.fit(texts, 12, 0)
    counts = cs.vectoriser().transform(texts)
    dense = normalize(TfidfTransformer().fit(counts).transform(counts)).toarray()
    expected = np.sqrt(((dense - fitted.centroids[fitted.labels]) ** 2).sum(axis=1))
    assert np.array_equal(fitted.distances, expected)


def test_neither_path_densifies_the_whole_matrix(monkeypatch: pytest.MonkeyPatch) -> None:
    """2,000 rows dense are 2,000 x 32,768 x 8 bytes = 524 MB; the peak stays far below."""
    monkeypatch.setattr(cs, "DISTANCE_CHUNK", 64)
    texts = sentences(2000, 4)
    tracemalloc.start()
    try:
        fitted = cs.fit(texts, 12, 0)
        cs.assign_matrix(texts, fitted.idf, fitted.centroids)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 150 * 2**20, f"peak {peak / 2**20:.0f} MB"
