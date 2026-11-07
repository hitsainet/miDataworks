"""The split plan's pure rules (FTASKS 7.1, 7.4)."""

from __future__ import annotations

import pytest

from src.services.curation.split_plan import SplitRefusal, SplitSpec, plan_split

TRAIN_TEST = [SplitSpec("train", 0.9, False), SplitSpec("test", 0.1, True)]


def test_round_not_floor_per_stratum() -> None:
    strata = ["a"] * 36 + ["b"] * 18  # 3.6 -> 4, 1.8 -> 2 (floor would give 3 and 1)
    plan = plan_split(strata, None, [None] * 54, TRAIN_TEST, 1)
    assert plan.counts["a"]["test"] == 4 and plan.counts["b"]["test"] == 2
    assert plan.counts["a"]["train"] == 32


def test_python_round_half_to_even() -> None:
    plan = plan_split(["a"] * 25, None, [None] * 25, TRAIN_TEST, 1)  # 2.5 -> 2
    assert plan.counts["a"]["test"] == 2


def test_fractions_must_sum_to_one() -> None:
    with pytest.raises(SplitRefusal) as exc:
        plan_split(
            ["a"] * 10,
            None,
            [None] * 10,
            [SplitSpec("t", 0.8, False), SplitSpec("e", 0.1, True)],
            1,
        )
    assert exc.value.code == "split_fractions_invalid"


def test_generated_never_held_out() -> None:
    origins = ["generated"] * 20
    with pytest.raises(SplitRefusal) as exc:
        plan_split(["a"] * 20, None, origins, TRAIN_TEST, 1)
    assert exc.value.code == "generated_in_held_out"


def test_group_never_spans_splits() -> None:
    groups = [f"g{i // 2}" for i in range(40)]  # pairs
    plan = plan_split(["a"] * 40, groups, [None] * 40, TRAIN_TEST, 3)
    names = plan.names()
    for g in set(groups):
        assert len({names[i] for i in range(40) if groups[i] == g}) == 1
    assert plan.counts["a"]["test"] == 4


def test_group_larger_than_target_is_reported_not_split() -> None:
    groups = ["big"] * 30 + [f"s{i}" for i in range(10)]
    plan = plan_split(["a"] * 40, groups, [None] * 40, TRAIN_TEST, 2)
    names = plan.names()
    assert {names[i] for i in range(30)} == {"train"}  # 30 rows cannot fit a target of 4
    assert plan.counts["a"]["test"] == 4 and plan.shortfalls == []


def test_unfillable_target_is_reported() -> None:
    groups = ["g1"] * 6 + ["g2"] * 6
    plan = plan_split(["a"] * 12, groups, [None] * 12, TRAIN_TEST, 2)  # target 1, groups of 6
    assert plan.shortfalls == [{"stratum": "a", "split": "test", "target": 1, "assigned": 0}]


def test_group_spanning_strata_takes_its_first_rows_stratum() -> None:
    strata = ["a", "b"] * 10
    groups = [f"g{i // 2}" for i in range(20)]
    plan = plan_split(strata, groups, [None] * 20, TRAIN_TEST, 1)
    names = plan.names()
    for g in set(groups):
        assert len({names[i] for i in range(20) if groups[i] == g}) == 1


def test_stratum_smaller_than_split_count_goes_to_first() -> None:
    plan = plan_split(["a"] * 20 + ["tiny"], None, [None] * 21, TRAIN_TEST, 1)
    assert plan.small_strata == ["tiny"] and plan.counts["tiny"] == {"train": 1}
