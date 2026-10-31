"""The position → row-key join of miStudio's per-row scores (011 FR-011.15, FR-011.17; D2, D14).

The published split and the served rows DIFFER in order and coverage on purpose: miStudio drops
rows (filters, failed renders), so the served positions are sparse and a join by sequence would
pair scores with the wrong rows."""

from __future__ import annotations

from typing import Any

from src.services.detector_sets import mistudio_scores
from src.services.detector_sets.mistudio_scores import Refusal, join_rows

MAPPING = {"humorous": "positive", "not_humorous": "negative", "unsure": "excluded"}

#: the published split: 8 rows; row 2 is excluded by the mapping, pair groups of two
KEYS = [f"k{i}" for i in range(8)]
OCC = [0, 0, 0, 0, 0, 0, 1, 0]
VALUES = [
    "humorous",
    "not_humorous",
    "unsure",
    "humorous",
    "not_humorous",
    "humorous",
    "not_humorous",
    "humorous",
]
PAIRS = ["g0", "g0", "g1", "g1", "g2", "g2", "g3", "g3"]


def served(*positions: int, labels: dict[int, int] | None = None) -> list[dict[str, Any]]:
    default = {0: 1, 1: 0, 3: 1, 4: 0, 5: 1, 6: 0, 7: 1}
    labels = labels or {}
    return [
        {
            "position": p,
            "score": p / 10,
            "label": labels.get(p, default.get(p, 0)),
            "n_scored": 5,
        }
        for p in positions
    ]


def run(rows: list[dict[str, Any]], **over: Any) -> Any:
    args: dict[str, Any] = {
        "keys": KEYS,
        "occurrences": OCC,
        "values": VALUES,
        "pairs": PAIRS,
        "mapping": MAPPING,
    }
    args.update(over)
    return join_rows(rows, **args)


def test_sparse_positions_join_by_position_not_by_sequence() -> None:
    # miStudio kept rows 7, 4, 1 and 5 (in that order; 0, 2, 3 and 6 dropped)
    got = run(served(7, 4, 1, 5))
    assert not isinstance(got, Refusal), got
    scores, labels, groups, keys = got
    assert keys == [("k7", 0), ("k4", 0), ("k1", 0), ("k5", 0)]
    assert scores == [0.7, 0.4, 0.1, 0.5]
    assert labels == [True, False, False, True]
    # FR-011.17: groups come from miDataworks' own pair column at the joined rows
    assert groups == ["g3", "g2", "g0", "g2"]


def test_the_occurrence_is_carried_with_the_key() -> None:
    got = run(served(6))
    assert not isinstance(got, Refusal)
    assert got[3] == [("k6", 1)]


def test_a_position_out_of_range_is_refused() -> None:
    got = run(served(1, 8))
    assert isinstance(got, Refusal)
    assert "position 8" in got.reason and "8 rows" in got.reason


def test_a_negative_position_is_refused() -> None:
    got = run([{"position": -1, "score": 0.5, "label": 1, "n_scored": 1}])
    assert isinstance(got, Refusal)


def test_a_label_disagreement_is_refused_never_coerced() -> None:
    got = run(served(0, 1, labels={1: 1}))
    assert isinstance(got, Refusal)
    assert "position 1" in got.reason and "k1" in got.reason and "negative" in got.reason


def test_a_served_row_this_role_excludes_is_a_disagreement() -> None:
    got = run(served(2, labels={2: 0}))
    assert isinstance(got, Refusal)
    assert "excluded" in got.reason


def test_a_role_without_a_pair_column_has_no_groups() -> None:
    got = run(served(0, 1), pairs=None)
    assert not isinstance(got, Refusal)
    assert got[2] == [None, None]


def test_label_of_target_is_mistudios_mapped_label() -> None:
    """035 FTDD 5.4: ``label`` is the mapped label of a kept row (int8), positive 1, negative 0."""
    assert mistudio_scores.LABEL_OF_TARGET == {"positive": 1, "negative": 0}
