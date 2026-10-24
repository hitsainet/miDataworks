"""Stratified sampling (006 FTASKS 8.4)."""

from __future__ import annotations

import pytest

from src.services.review.sampling import allocate, stratified_sample


def rows(counts: dict[str, int]) -> list[tuple[str, str]]:
    out = []
    for stratum, n in counts.items():
        out += [(f"{stratum}-{i:04d}", stratum) for i in range(n)]
    return out


def test_proportional_with_largest_remainder_and_exact_size() -> None:
    alloc = allocate({"a": 500, "b": 300, "c": 200}, 100)
    assert alloc == {"a": 50, "b": 30, "c": 20}
    alloc = allocate({"a": 333, "b": 333, "c": 334}, 100)
    assert sum(alloc.values()) == 100 and alloc["c"] == 34


def test_at_least_one_per_non_empty_stratum() -> None:
    alloc = allocate({"big": 9990, "rare": 10, "empty": 0}, 50)
    assert alloc["rare"] >= 1 and alloc["empty"] == 0 and sum(alloc.values()) == 50


def test_fewer_rows_than_size_takes_everything() -> None:
    sample = stratified_sample(rows({"a": 10, "b": 5}), 100, seed=1)
    assert len(sample) == 15


def test_exact_size_and_deterministic_by_seed() -> None:
    data = rows({"positive|at_or_above": 120, "negative|at_or_below": 400, "x|excluded": 80})
    a = stratified_sample(data, 100, seed=7)
    b = stratified_sample(list(reversed(data)), 100, seed=7)
    c = stratified_sample(data, 100, seed=8)
    assert len(a) == 100 and a == b and a != c
    assert len({k for k, _ in a}) == 100


def test_refuses_size_zero() -> None:
    with pytest.raises(ValueError):
        stratified_sample(rows({"a": 3}), 0, seed=1)
