"""Connected components over several group columns (017 FTASKS 2.1; FR-017.1)."""

from __future__ import annotations

from src.services.curation.components import components, largest


def test_two_columns_chain_three_rows_into_one_unit() -> None:
    # row 0 and 1 share a carrier; rows 1 and 2 share a pair: one unit of three, row 3 alone
    carrier = ["c1", "c1", "c2", "c3"]
    pair = [None, "p1", "p1", None]
    units = components(4, [carrier, pair])
    assert units == [[0, 1, 2], [3]]
    assert largest(units) == 3


def test_one_column_gives_the_old_group_units_in_first_row_order() -> None:
    # split@1.0.0's units: g:<value> per group, r:<i> for a null, ordered by first row
    groups = ["b", None, "a", "b", "a", None]
    assert components(6, [groups]) == [[0, 3], [1], [2, 4], [5]]


def test_no_column_is_one_row_per_unit_and_nothing_is_empty() -> None:
    assert components(3, []) == [[0], [1], [2]]
    assert components(0, []) == [] and largest([]) == 0


def test_a_late_link_merges_two_earlier_units() -> None:
    a = ["x", "y", None, "x"]
    b = [None, "q", "q", "q"]  # row 3 joins x (row 0) to q (rows 1, 2)
    assert components(4, [a, b]) == [[0, 1, 2, 3]]
