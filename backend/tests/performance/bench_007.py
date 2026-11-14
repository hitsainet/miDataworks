"""Feature 007 benchmark (FTASKS 9.9). Run by hand, not collected by pytest (not test_*.py):

    cd backend && python -m tests.performance.bench_007

The figure is measured on the path the report runs (global rule: benchmark the changed path):
``diversity_service.figures_for`` and ``controls_for`` on 5,000 rows per side, lexical only (no
embeddings), 1,000 subsamples per interval, clusters assigned by 004's real ``fit``/``assign``.
Target: <= 30 s (FTDD 007 section 9).

``python -m tests.performance.bench_007 --embeddings 1024`` adds spread on seeded 1,024-dimension
vectors (the path method version 2 changed: eight positive-control splits, and ``spread_ci``
normalising rows once). Measured 2026-10-19 in FTASKS 9.9.

Method version 3 (proposed 2026-10-19) adds ``share_labels_for``: one more lexical fit, on the
multiset union of both samples (up to 10,000 rows here), timed as ``share refit``.
``--method 2`` runs the path without it, for the before/after.
"""

from __future__ import annotations

import random
import sys
import time

import numpy as np

from src.services.curation import cluster_service
from src.services.generation import diversity_service

N = 5000
RESAMPLES = 1000
K = 50


def corpus(n: int, seed: int) -> list[str]:
    rng = random.Random(seed)  # noqa: S311 - a benchmark corpus
    topics = [[f"t{t}w{i}" for i in range(200)] for t in range(K)]
    return [" ".join(rng.choice(topics[i % K]) for _ in range(30)) for i in range(n)]


def vectors(n: int, dim: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (
        rng.normal(size=(n, dim)) + 1.2
    )  # a shared offset: spread about 0.2, as served models give


def main(dim: int = 0, method: int = diversity_service.METHOD_VERSION) -> dict[str, float]:
    reference = corpus(N, 1)
    version = corpus(N, 2)
    r_vec = vectors(N, dim, 1) if dim else None
    v_vec = vectors(N, dim, 2) if dim else None
    started = time.perf_counter()
    fitted = cluster_service.fit(reference, K, 7)
    r_labels, _ = cluster_service.assign_matrix(reference, fitted.idf, fitted.centroids)
    v_labels, _ = cluster_service.assign_matrix(version, fitted.idf, fitted.centroids)
    clusters = time.perf_counter() - started
    v_share = r_share = None
    if method >= 3:
        v_share, r_share, _ = diversity_service.share_labels_for(version, reference, K, 7)
    refit = time.perf_counter() - started - clusters
    figures = diversity_service.figures_for(
        version,
        reference,
        v_vec,
        r_vec,
        v_labels,
        r_labels,
        fitted.k,
        7,
        RESAMPLES,
        v_share,
        r_share,
    )
    after_figures = time.perf_counter()
    controls = diversity_service.controls_for(reference, r_vec, r_labels, fitted.k, 7, RESAMPLES)
    ended = time.perf_counter()
    total = ended - started
    print(
        f"method {method}, embeddings {dim or 'none'}: clusters {clusters:.2f} s; share refit "
        f"{refit:.2f} s; figures {after_figures - started - clusters - refit:.2f} s; controls "
        f"{ended - after_figures:.2f} s; "
        f"report {total:.2f} s (target <= 30 s)"
    )
    assert {c["result"] for c in controls} == {"pass"}, controls
    del figures
    return {"clusters_s": clusters, "refit_s": refit, "report_s": total}


def _arg(name: str, default: int) -> int:
    return int(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else default


if __name__ == "__main__":
    main(_arg("--embeddings", 0), _arg("--method", diversity_service.METHOD_VERSION))
