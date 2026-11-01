"""Pure diversity figures with hand-computed values (007 FTASKS 3.10, 3.11)."""

from __future__ import annotations

import numpy as np
import pytest

from src.services.generation import diversity_metrics as dm

FIVE = ["the cat sat", "the cat ran", "a dog sat", "the cat sat", "birds fly high"]


def test_distinct_1_and_2_by_hand() -> None:
    # tokens: the cat sat | the cat ran | a dog sat | the cat sat | birds fly high -> 15 tokens
    # distinct unigrams: the cat sat ran a dog birds fly high -> 9
    assert dm.distinct_n(FIVE, 1) == pytest.approx(9 / 15)
    # bigrams: (the,cat)(cat,sat) (the,cat)(cat,ran) (a,dog)(dog,sat) (the,cat)(cat,sat)
    # (birds,fly)(fly,high) -> 10 total; distinct 7
    assert dm.distinct_n(FIVE, 2) == pytest.approx(7 / 10)


def test_the_lex_v1_tokeniser_lowercases_and_splits_on_non_words() -> None:
    assert dm.tokens("Hello, World! it's") == ["hello", "world", "it", "s"]
    assert dm.distinct_n(["", "!!"], 1) is None


def test_spread_on_identical_and_orthogonal_vectors() -> None:
    assert dm.centroid_spread(np.ones((4, 3))) == pytest.approx(0.0)
    # e1, e2: centroid (0.5, 0.5) normalised (1/√2, 1/√2); cos = 1/√2 each
    assert dm.centroid_spread(np.eye(2)) == pytest.approx(1 - 1 / np.sqrt(2))


def test_coverage_by_hand() -> None:
    occupied, largest = dm.cluster_coverage([0, 0, 1, 3], 5)
    assert occupied == pytest.approx(3 / 5) and largest == pytest.approx(2 / 4)


def ci(lo: float, hi: float) -> dm.Interval:
    return dm.Interval((lo + hi) / 2, lo, hi)


def test_falls_only_when_the_whole_interval_is_below() -> None:
    ref = ci(0.5, 0.7)
    assert dm.falls(ci(0.2, 0.49), ref, "higher_is_diverse")
    assert not dm.falls(ci(0.2, 0.55), ref, "higher_is_diverse"), "overlap is not a fall"
    assert not dm.falls(ci(0.6, 0.65), ref, "higher_is_diverse")


def test_falls_reversed_for_the_largest_cluster_share() -> None:
    ref = ci(0.2, 0.3)
    assert dm.falls(ci(0.31, 0.5), ref, "lower_is_diverse")
    assert not dm.falls(ci(0.25, 0.5), ref, "lower_is_diverse")


def test_a_point_estimate_never_decides() -> None:
    """Version value below the reference's but intervals overlapping: not a fall."""
    version = dm.Interval(0.4, 0.3, 0.6)
    reference = dm.Interval(0.5, 0.45, 0.55)
    assert not dm.falls(version, reference, "higher_is_diverse")


def _corpus(n: int, rng: np.random.Generator) -> list[str]:
    words = [f"w{i}" for i in range(400)]
    return [" ".join(rng.choice(words, size=12)) for _ in range(n)]


def test_a_collapse_falls_and_split_halves_do_not() -> None:
    rng = np.random.default_rng(0)
    texts = _corpus(400, rng)
    labels = rng.integers(0, 8, size=400)
    # make the largest cluster's rows near-identical so copying them narrows distinct-n
    big = int(np.bincount(labels).argmax())
    for i in np.flatnonzero(labels == big):
        texts[int(i)] = "same old line again and again"
    vectors = rng.normal(size=(400, 16))
    vectors[labels == big] = vectors[labels == big][0]
    collapsed = dm.collapse_sample(400, labels, np.random.default_rng(1))
    c_texts = [texts[int(i)] for i in collapsed]
    r1, r2 = np.random.default_rng(2), np.random.default_rng(3)
    v = dm.distinct_ci(c_texts, 2, r1, 200)
    r = dm.distinct_ci(texts, 2, r2, 200)
    assert dm.falls(v, r, "higher_is_diverse")
    vs = dm.spread_ci(vectors[collapsed], r1, 200)
    rs = dm.spread_ci(vectors, r2, 200)
    assert dm.falls(vs, rs, "higher_is_diverse")
    _, v_share = dm.coverage_ci(labels[collapsed], 8, r1, 200)
    _, r_share = dm.coverage_ci(labels, 8, r2, 200)
    assert dm.falls(v_share, r_share, "lower_is_diverse")
    a, b = dm.split_halves(400, np.random.default_rng(4))
    assert not set(a.tolist()) & set(b.tolist())
    ha = dm.distinct_ci([texts[int(i)] for i in a], 2, r1, 200)
    hb = dm.distinct_ci([texts[int(i)] for i in b], 2, r2, 200)
    assert not dm.falls(ha, hb, "higher_is_diverse")


def test_bootstrap_is_seeded() -> None:
    texts = _corpus(50, np.random.default_rng(5))
    one = dm.distinct_ci(texts, 1, np.random.default_rng(9), 100)
    two = dm.distinct_ci(texts, 1, np.random.default_rng(9), 100)
    assert one == two and one.lo is not None and one.lo <= one.value <= one.hi  # type: ignore[operator]
