"""A drop reason's counts by value (FU-7a gap (l), 2026-11-27): the pure grouping step_ingest uses."""

from __future__ import annotations

from src.services.step_ingest import REASON_VALUES_LISTED, reason_values


def test_two_to_the_cap_values_are_listed_by_count() -> None:
    grouped = [
        ("dropped", "value_excluded", "e_drop=framing", 3, "e_drop = 'framing' is excluded"),
        ("dropped", "value_excluded", "e_drop=leak", 5, "e_drop = 'leak' is excluded"),
        ("dropped", "other", "x=1", 7, "x"),
    ]
    out = reason_values(grouped)
    assert out[("dropped", "value_excluded")] == {
        "values": [
            {"value": "e_drop=leak", "count": 5, "example": "e_drop = 'leak' is excluded"},
            {"value": "e_drop=framing", "count": 3, "example": "e_drop = 'framing' is excluded"},
        ],
        "values_count": 2,
    }
    assert ("dropped", "other") not in out  # one value: its example names it


def test_more_values_than_the_cap_give_the_count_only() -> None:
    many = [("dropped", "dup", f"k{i}", 1, "dup") for i in range(REASON_VALUES_LISTED + 1)]
    assert reason_values(many) == {("dropped", "dup"): {"values_count": REASON_VALUES_LISTED + 1}}
    at_cap = many[:REASON_VALUES_LISTED]
    assert len(reason_values(at_cap)[("dropped", "dup")]["values"]) == REASON_VALUES_LISTED
