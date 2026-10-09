"""Feature 007 benchmark (FTASKS 9.9). Run by hand, not collected by pytest (not test_*.py):

    cd backend && python -m tests.performance.bench_007

The figure is measured on the path the report runs (global rule: benchmark the changed path):
``diversity_service.figures_for`` and ``controls_for`` on 5,000 rows per side, lexical only (no
embeddings), 1,000 subsamples per interval, clusters assigned by 004's real ``fit``/``assign``.
Target: <= 30 s (FTDD 007 section 9).
"""

from __future__ import annotations

import random
import time

from src.services.curation import cluster_service
from src.services.generation import diversity_service

N = 5000
RESAMPLES = 1000
K = 50


def corpus(n: int, seed: int) -> list[str]:
    rng = random.Random(seed)  # noqa: S311 - a benchmark corpus
    topics = [[f"t{t}w{i}" for i in range(200)] for t in range(K)]
    return [" ".join(rng.choice(topics[i % K]) for _ in range(30)) for i in range(n)]


def main() -> dict[str, float]:
    reference = corpus(N, 1)
    version = corpus(N, 2)
    started = time.perf_counter()
    fitted = cluster_service.fit(reference, K, 7)
    r_labels, _ = cluster_service.assign_matrix(reference, fitted.idf, fitted.centroids)
    v_labels, _ = cluster_service.assign_matrix(version, fitted.idf, fitted.centroids)
    clusters = time.perf_counter() - started
    figures = diversity_service.figures_for(
        version, reference, None, None, v_labels, r_labels, fitted.k, 7, RESAMPLES
    )
    controls = diversity_service.controls_for(reference, None, r_labels, fitted.k, 7, RESAMPLES)
    total = time.perf_counter() - started
    print(f"clusters {clusters:.2f} s; report {total:.2f} s (target <= 30 s)")
    assert {c["result"] for c in controls} == {"pass"}, controls
    del figures
    return {"clusters_s": clusters, "report_s": total}


if __name__ == "__main__":
    main()
