"""The cell balancer's pure decisions (FTASKS 6.1, 6.4)."""

from __future__ import annotations

import pytest

from src.services.curation import cells as c


def _expand(counts: dict[tuple[str, str], int]) -> tuple[list[str], list[str]]:
    values, labels = [], []
    for (v, lab), n in counts.items():
        values += [v] * n
        labels += [lab] * n
    return values, labels


def test_cap_is_the_smallest_cell_and_unequal_cells_are_cut_to_it() -> None:
    values, labels = _expand({("a", "x"): 7, ("a", "y"): 3, ("b", "x"): 4, ("b", "y"): 9})
    plan = c.cell_cap_plan(values, labels, exclude_values=[], seed=1)
    assert plan.cap == 3
    kept = [(v, lab) for v, lab, k in zip(values, labels, plan.kept_mask, strict=True) if k]
    for cell in {("a", "x"), ("a", "y"), ("b", "x"), ("b", "y")}:
        assert kept.count(cell) == 3
    assert plan.cells["a|x"] == {"value": "a", "label": "x", "before": 7, "after": 3}


def test_seeded_within_cell_draw() -> None:
    values, labels = _expand({("a", "x"): 10, ("a", "y"): 2, ("b", "x"): 5, ("b", "y"): 2})
    one = c.cell_cap_plan(values, labels, exclude_values=[], seed=5).decision.tolist()
    two = c.cell_cap_plan(values, labels, exclude_values=[], seed=5).decision.tolist()
    other = c.cell_cap_plan(values, labels, exclude_values=[], seed=6).decision.tolist()
    assert one == two and one != other


def test_excluded_values_are_dropped_before_capping() -> None:
    values, labels = _expand(
        {("a", "x"): 5, ("a", "y"): 5, ("b", "x"): 6, ("b", "y"): 6, ("n", "y"): 9}
    )
    plan = c.cell_cap_plan(values, labels, exclude_values=["n"], seed=1)
    assert plan.cap == 5 and plan.excluded_values == ["n"]
    assert (plan.decision == c.VALUE_EXCLUDED).sum() == 9


def test_extreme_value_flag_reddit_news() -> None:
    counts = {
        ("news", "humorous"): 3,
        ("news", "not_humorous"): 1387,
        ("joke", "humorous"): 50,
        ("joke", "not_humorous"): 9,
    }
    flags = c.extreme_values(c.cell_table(*_expand(counts), ["humorous", "not_humorous"]))
    assert [f["value"] for f in flags] == ["news"]
    assert flags[0]["rows"] == 1390 and flags[0]["dominant_rows"] == 1387


def test_small_values_are_not_flagged() -> None:
    counts = {("rare", "x"): 40, ("rare", "y"): 0}
    assert c.extreme_values(c.cell_table(*_expand(counts), ["x", "y"])) == []


def test_empty_cell_refuses_naming_value_and_label() -> None:
    values, labels = _expand({("a", "x"): 5, ("a", "y"): 5, ("b", "x"): 5})
    with pytest.raises(c.CellRefusal) as exc:
        c.cell_cap_plan(values, labels, exclude_values=[], seed=1)
    assert exc.value.code == "empty_cell"
    assert exc.value.details["value"] == "b" and exc.value.details["label"] == "y"
    assert "Exclude the value 'b'" in exc.value.message


def test_single_class_refuses() -> None:
    with pytest.raises(c.CellRefusal) as exc:
        c.cell_cap_plan(["a", "b"], ["x", "x"], exclude_values=[], seed=1)
    assert exc.value.code == "single_class_label"
