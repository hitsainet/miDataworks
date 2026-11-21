"""The diversity report's method version 3: the largest share from a refit on both samples.

Proposed 2026-10-19, PENDING OPERATOR (FPRD 007 Q32, FTDD 007 §6.6 and TQ22; record
``0xcc/reviews/007_largest_share_v3_2026-10-19.md``). Up to method version 2, the largest-cluster
share read the reference's clustering: centroids fitted on the reference rows, version rows
placed at the nearest one. The centroids are in-sample for the reference and out-of-sample for
the version's new rows. A new row that shares few words with every centroid goes to the centroid
with the smallest norm, the most diffuse one, which is usually the largest cluster. So the
version's largest share rose by construction: on ColBERT jokes it "fell" for most versions that
only added fresh jokes from the reference's own pool. Version 3 (``union_refit_v1``) fits one
clustering on the multiset union of both samples, so both sides are in-sample.

Two corpora. ``zipf``: words from a large Zipf vocabulary with weak topics, so most of a new row's
words are rare and many are unseen by the reference; this is where the artefact lives, and
``test_the_seeds_are_adversarial_for_the_reference_assignment`` proves it on these seeds instead
of trusting a list. Its lexical clusters are poor (the largest holds about 40% of the reference),
so it cannot show power. ``topics``: the integration fixture's corpus (eight topics with their own
vocabularies, k = 12), where the largest cluster holds about a tenth of the reference, for the
power test and a second null. The real-shaped numbers (ColBERT jokes, k = 50, 1,000 resamples)
are in the record; these are the fixed-seed guards.
"""

from __future__ import annotations

import random
from functools import cache, lru_cache

import numpy as np
import pytest

from src.services.curation.cluster_service import assign_matrix, fit
from src.services.generation import diversity_metrics as dm
from src.services.generation import diversity_service as ds

RESAMPLES = 200
K = 20
N_REFERENCE, N_ADDED = 300, 100
VOCAB, ZIPF, TOPICS, TOPIC_WORDS, P_TOPIC = 20_000, 1.0, 30, 40, 0.3
FAMILY_SHARE = 0.3
SEEDS = tuple(range(24))
POWER_SEEDS = tuple(range(20))


@lru_cache(maxsize=1)
def _weights() -> np.ndarray:
    w = 1.0 / np.arange(1, VOCAB + 1) ** ZIPF
    return np.asarray(w / w.sum())


def _word(rng: np.random.Generator) -> str:
    return f"w{int(rng.choice(VOCAB, p=_weights()))}"


def _row(rng: np.random.Generator) -> list[str]:
    topic = int(rng.integers(TOPICS))
    words = []
    for _ in range(int(rng.integers(8, 17))):
        if rng.random() < P_TOPIC:
            words.append(f"t{topic}x{int(rng.integers(TOPIC_WORDS))}")
        else:
            words.append(_word(rng))
    return words


@lru_cache(maxsize=64)
def pool(seed: int) -> tuple[str, ...]:
    """Reference rows then fresh rows, all drawn alike (one pool)."""
    rng = np.random.default_rng(1_000 + seed)
    return tuple(" ".join(_row(rng)) for _ in range(N_REFERENCE + N_ADDED))


def reference_labels(
    reference: list[str], version: list[str], seed: int, k: int = K
) -> tuple[np.ndarray, np.ndarray, int]:
    """Method version 2's labels: the reference's clustering, version rows at the nearest centroid."""
    fitted = fit(reference, k, seed)
    r_lab = assign_matrix(reference, fitted.idf, fitted.centroids)[0]
    v_lab = assign_matrix(version, fitted.idf, fitted.centroids)[0]
    return v_lab, r_lab, int(fitted.k)


def share(
    version: list[str], reference: list[str], seed: int, method: int, k: int
) -> dict[str, object]:
    """The largest-share figure exactly as ``compute`` builds it, under method 2 or 3."""
    v_lab, r_lab, n_clusters = reference_labels(reference, version, seed, k)
    if method == 3:
        v_share, r_share, _ = ds.share_labels_for(version, reference, k, seed)
    else:
        v_share = r_share = None
    figures = ds.figures_for(
        version, reference, None, None, v_lab, r_lab, n_clusters, seed, RESAMPLES, v_share, r_share
    )
    return dict(figures["largest_cluster_share"])


def same_pool(seed: int) -> tuple[list[str], list[str]]:
    """``zipf``: (version = reference + fresh rows from the same pool, reference)."""
    rows = list(pool(seed))
    return rows, rows[:N_REFERENCE]


FIXTURE_TOPICS = [[f"t{t}w{i}" for i in range(80)] for t in range(8)]
FIXTURE_K = 12  # above the eight topics: with k = 8 a seed can merge topics (a k-means optimum)


def sentences(n: int, seed: int) -> list[str]:
    """``topics``: the integration fixture's rows (14 words of one topic, topics in turn)."""
    rng = random.Random(seed)  # noqa: S311 - a test corpus
    return [" ".join(rng.choice(FIXTURE_TOPICS[i % 8]) for _ in range(14)) for i in range(n)]


def topics_same_pool(seed: int) -> tuple[list[str], list[str]]:
    reference = sentences(N_REFERENCE, 100 + seed)
    return reference + sentences(N_ADDED, 200 + seed), reference


def concentrated(seed: int) -> tuple[list[str], list[str]]:
    """``topics``: (version = reference + copies of one family of five near-duplicates, two
    words apart, making ``FAMILY_SHARE`` of the version, reference): a planted concentration."""
    rng = random.Random(5_000 + seed)  # noqa: S311 - a test corpus
    reference = sentences(N_REFERENCE, 100 + seed)
    anchor = sentences(1, 300 + seed)[0].split()
    family = []
    for _ in range(5):
        words = list(anchor)
        for i in rng.sample(range(len(words)), 2):
            words[i] = rng.choice(FIXTURE_TOPICS[0])
        family.append(" ".join(words))
    n_copies = int(round(FAMILY_SHARE * N_REFERENCE / (1.0 - FAMILY_SHARE)))
    return reference + [rng.choice(family) for _ in range(n_copies)], reference


@cache
def falls_on(corpus: str, seed: int, method: int) -> bool:
    if corpus == "zipf":
        version, reference = same_pool(seed)
        return share(version, reference, seed, method, K)["verdict"] == "falls"
    version, reference = topics_same_pool(seed)
    return share(version, reference, seed, method, FIXTURE_K)["verdict"] == "falls"


def test_the_seeds_are_adversarial_for_the_reference_assignment() -> None:
    """Method version 2's assignment reads a fall on many same-pool additions (the artefact)."""
    falls = [s for s in SEEDS if falls_on("zipf", s, 2)]
    assert len(falls) >= len(SEEDS) // 5, falls


def test_same_pool_additions_rarely_fall() -> None:
    """Version 3's false-fall rate on same-pool additions is bounded: two 95% intervals of one
    quantity miss each other on about 0.3% of draws (measured on jokes: 0 of 600); allow one
    seed per set."""
    for corpus in ("zipf", "topics"):
        falls = [s for s in SEEDS if falls_on(corpus, s, 3)]
        assert len(falls) <= 1, (corpus, falls)


def test_a_planted_concentration_still_falls() -> None:
    """Power: 30% of the version are copies of one near-duplicate family."""
    falls = [
        s for s in POWER_SEEDS if share(*concentrated(s), s, 3, FIXTURE_K)["verdict"] == "falls"
    ]
    assert len(falls) >= len(POWER_SEEDS) - 1, falls


def test_the_other_figures_are_unchanged() -> None:
    """The share's labels change; nothing else does (the draws are the same, by rng position)."""
    version, reference = same_pool(0)
    v_lab, r_lab, n_clusters = reference_labels(reference, version, 0)
    vectors = np.random.default_rng(0).normal(size=(len(version), 8))
    v_share, r_share, _ = ds.share_labels_for(version, reference, K, 0)
    args = (version, reference, vectors, vectors[:N_REFERENCE], v_lab, r_lab, n_clusters, 0, 50)
    old = ds.figures_for(*args)
    new = ds.figures_for(*args, v_share, r_share)
    assert set(old) == set(new)
    for key in old:
        if key != "largest_cluster_share":
            assert old[key] == new[key], key
    assert new["largest_cluster_share"] != old["largest_cluster_share"]


def test_coverage_ci_reads_the_share_from_its_own_labels() -> None:
    labels = np.array([0, 0, 1, 2, 3, 3, 3, 1])
    other = np.array([5, 5, 5, 5, 5, 5, 6, 7])
    cov_a, share_a = dm.coverage_ci(labels, 8, np.random.default_rng(3), 50)
    cov_b, share_b = dm.coverage_ci(labels, 8, np.random.default_rng(3), 50, None, other)
    assert cov_a == cov_b
    assert share_b.value is not None and share_a.value is not None
    assert share_b.value > share_a.value
    with pytest.raises(ValueError, match="same rows"):
        dm.coverage_ci(labels, 8, np.random.default_rng(3), 50, None, other[:-1])


def test_the_fit_reads_each_distinct_text_once_and_every_row_takes_its_label() -> None:
    """A reference row the version inherits, and a text the version repeats, are each ONE point
    of the fit; every row then takes its text's label, so a repeat weighs in the share as often
    as it occurs. (Fitted on every row, a repeated text pulled a centroid to itself: a family of
    five near-copies took five centroids, split its share five ways, and starved the reference's
    rows of centroids; that variant read 0 of 300 planted concentrations on jokes as a fall.)"""
    reference = ["a b c", "d e f", "g h i"]
    version = [*reference, "x y z", "x y z", "x y z"]
    v_lab, r_lab, info = ds.share_labels_for(version, reference, 2, 0)
    assert info == {"method": "union_distinct_refit_v1", "basis": "lexical", "k": 2, "texts": 4}
    assert len(v_lab) == 6 and len(r_lab) == 3
    assert v_lab[:3] == r_lab  # the same text has the same label on both sides
    assert len(set(v_lab[3:])) == 1
    assert ds.share_labels_for(["a b"], ["a b", "a b", "c d"], 2, 0)[2]["texts"] == 2
    assert ds.share_labels_for([], [], 2, 0)[2]["texts"] == 0


def test_every_report_records_the_largest_share_method() -> None:
    method = ds.method_for("completion", ["train"], 300, 7, RESAMPLES, 8, None)
    assert method["method_version"] == ds.METHOD_VERSION == 3
    assert method["largest_share"] == {
        "method": "union_distinct_refit_v1",
        "basis": "lexical",
        "k": 8,
    }
    # the controls are unchanged from version 2
    assert method["positive_control"]["method"] == "split_half_calibration_v2"
    # an older report keeps its version
    assert ds.method_version_of({**method, "method_version": 2}) == 2
    assert ds.method_version_of({"interval": "half_subsample_percentile_v1"}) == 1
