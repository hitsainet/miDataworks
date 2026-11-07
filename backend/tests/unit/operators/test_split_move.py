"""``split@1.2.0`` mode ``move``: only the chosen rows change split (ENH-006 G32, 2026-11-07).

The D128 stack needed 20 positives of four small forms in the in-distribution test. Every split
mode redrew every row, so the run split the positives alone, joined them back to the negatives,
and D-4's repair then dropped the moved rows' near-duplicate siblings left in training. ``move``
takes a seeded share of the matching rows (with their groups) and leaves every other row's split.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest

from src.operators.errors import StepFailed
from tests.fixtures.humor_pool import with_system_columns
from tests.support.curation_fixtures import registry, run_operator

ROLES = {"text": "content"}


def _table() -> pa.Table:
    """200 rows: 80 positives of two forms in train with a sibling each (same pair), 20 held-out
    positives in test_unseen, and negatives in train and id_test."""
    rows: list[dict[str, Any]] = []
    splits: list[str] = []
    for i in range(40):
        for form in ("spelled", "digits"):
            pair = f"p-{form}-{i}"
            rows.append({"text": f"pos {form} {i}", "kind": "pos", "form": form, "pair": pair})
            splits.append("train")
            rows.append({"text": f"sib {form} {i}", "kind": "neg", "form": form, "pair": pair})
            splits.append("train")
    for i in range(20):
        rows.append({"text": f"held {i}", "kind": "pos", "form": "spelled", "pair": f"h-{i}"})
        splits.append("test_unseen")
    for i in range(20):
        rows.append({"text": f"neg {i}", "kind": "neg", "form": "spelled", "pair": None})
        splits.append("train" if i < 15 else "id_test")
    table = with_system_columns(rows)
    return table.set_column(
        table.schema.get_field_index("_dw_split"), "_dw_split", pa.array(splits, pa.string())
    )


class _Ran:
    def __init__(self, table: pa.Table, params: dict[str, Any], seed: int = 20261005) -> None:
        sink: list[dict[str, Any]] = []
        ran = run_operator(
            "split", params, table, ROLES, version="1.2.0", seed=seed, report_sink=sink
        )
        self.output = ran.output
        self.report = sink[-1]


def _move(table: pa.Table, **over: Any) -> Any:
    params = {
        "mode": "move",
        "move_where": "kind == pos and form == spelled",
        "move_from": ["train"],
        "move_to": "id_test",
        "move_fraction": 0.5,
        "stratify_by": ["form"],
        **over,
    }
    return _Ran(table, params)


def _by_text(table: pa.Table) -> dict[str, str]:
    return dict(
        zip(table.column("text").to_pylist(), table.column("_dw_split").to_pylist(), strict=True)
    )


def test_only_the_matching_rows_move_and_every_other_row_keeps_its_split(data_dir: Path) -> None:
    before = _table()
    ran = _move(before)
    old, new = _by_text(before), _by_text(ran.output)
    changed = {t for t in old if old[t] != new[t]}
    assert len(changed) == 20  # half of the 40 spelled positives in train
    assert all(t.startswith("pos spelled") for t in changed)
    assert {new[t] for t in changed} == {"id_test"}
    assert ran.report["moved_rows"] == 20 and ran.report["moved_with_group"] == 0
    assert ran.output.num_rows == before.num_rows
    assert "split_roles" not in ran.report  # the parent's held-out marks are inherited


def test_with_a_group_column_the_siblings_move_with_their_row(data_dir: Path) -> None:
    ran = _move(_table(), group_columns=["pair"])
    new = _by_text(ran.output)
    moved = [t for t, s in new.items() if s == "id_test" and not t.startswith("neg")]
    assert len(moved) == 40
    for text in moved:
        if text.startswith("pos"):
            assert new["sib" + text[3:]] == "id_test", "a sibling stayed in training"
    assert ran.report["moved_matching_rows"] == 20 and ran.report["moved_with_group"] == 20


def test_held_out_rows_never_move_and_a_group_reaching_them_is_blocked(data_dir: Path) -> None:
    table = _table()
    # tie one training positive to a held-out row through the pair column
    pairs = table.column("pair").to_pylist()
    texts = table.column("text").to_pylist()
    pairs[texts.index("pos spelled 0")] = "h-0"
    pairs[texts.index("sib spelled 0")] = "h-0"
    table = table.set_column(table.schema.get_field_index("pair"), "pair", pa.array(pairs))
    ran = _move(table, group_columns=["pair"], move_fraction=1.0)
    new = _by_text(ran.output)
    assert all(new[f"held {i}"] == "test_unseen" for i in range(20))
    assert new["pos spelled 0"] == "train"
    assert ran.report["groups_blocked_outside_move_from"] == 1


def test_the_move_is_a_function_of_the_seed(data_dir: Path) -> None:
    a = _by_text(_move(_table(), move_fraction=0.25).output)
    b = _by_text(_move(_table(), move_fraction=0.25).output)
    params = {
        "mode": "move",
        "move_where": "kind == pos and form == spelled",
        "move_to": "id_test",
        "move_fraction": 0.25,
    }
    c = _by_text(_Ran(_table(), params, seed=7).output)
    assert a == b
    assert a != c
    assert Counter(a.values()) == Counter(c.values())


def test_a_move_into_a_split_the_rows_do_not_use_is_refused(data_dir: Path) -> None:
    with pytest.raises(StepFailed, match="No row is in split"):
        _move(_table(), move_to="nowhere")


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        (
            {"mode": "move", "move_where": "kind == pos", "move_to": "train", "move_fraction": 0.5},
            {"/move_to": "must not be one of move_from"},
        ),
        (
            {"mode": "move", "move_where": "kind == pos", "move_to": "id_test"},
            {"/move_fraction": "mode move needs it"},
        ),
        ({"mode": "reassign", "move_to": "id_test"}, {"/move_to": "applies only to mode move"}),
    ],
)
def test_bad_move_parameters_are_refused_not_ignored(
    params: dict[str, Any], expected: dict[str, str]
) -> None:
    errors = registry().validate_params_detailed("split", "1.2.0", params)
    assert {e["pointer"]: e["message"] for e in errors} == expected


def test_split_1_2_0_is_current_and_1_1_0_still_runs() -> None:
    live = registry()
    assert live.current_version("split") == "1.2.0"
    assert live.is_allowed("split", "1.1.0")
