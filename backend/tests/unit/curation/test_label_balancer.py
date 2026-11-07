"""``label_balancer`` through 003's real executor (004 FR-004.52–004.57; FTASKS 18.1, 18.2).

The humour pool has real, unequal classes (6,348 not humorous / 5,457 humorous), so equal classes
after the step are the operator's work, not the fixture's."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pyarrow as pa
import pytest

from src.operators.errors import StepFailed
from src.services.curation.label_balance import (
    LEAKAGE_WARNING,
    LabelBalanceRefusal,
    label_balance_plan,
)
from tests.fixtures import humor_pool as hp
from tests.support.curation_fixtures import run_operator

EXCL = ["label_probability"]


def _with_splits(table: pa.Table, test_every: int = 10) -> pa.Table:
    splits = ["test" if i % test_every == 0 else "train" for i in range(table.num_rows)]
    index = table.schema.get_field_index("_dw_split")
    return table.set_column(index, "_dw_split", pa.array(splits, pa.string()))


def _counts(table: pa.Table) -> Counter:
    return Counter(
        zip(table.column("_dw_split").to_pylist(), table.column("label").to_pylist(), strict=True)
    )


def test_every_class_is_sampled_to_the_smallest(data_dir: Path) -> None:
    sink: list[dict] = []
    pool = hp.labelled_pool()
    assert Counter(pool.column("label").to_pylist()) == {"not_humorous": 6348, "humorous": 5457}
    ran = run_operator(
        "label_balancer", {"exclude_columns": EXCL}, pool, hp.ROLES, report_sink=sink
    )
    assert Counter(ran.output.column("label").to_pylist()) == {
        "not_humorous": 5457,
        "humorous": 5457,
    }
    caps = ran.events_by_reason("label_cap")
    assert len(caps) == 6348 - 5457
    assert caps[0]["statistic_name"] == "class_size" and caps[0]["statistic_value"] == 6348
    assert '"comparator":"sampled_to"' in caps[0]["threshold"]
    report = sink[0]
    assert report["caps"] == {"train": 5457}
    assert report["leakage_warning"] == LEAKAGE_WARNING
    assert report["leakage_warning"]["code"] == "label_balanced"
    assert "columns" in report["reaudit"]  # the kept rows are re-audited (FR-004.55)


def test_each_split_is_balanced_against_itself_and_no_row_moves(data_dir: Path) -> None:
    table = _with_splits(hp.labelled_pool())
    before = dict(
        zip(
            table.column("_dw_row_key").to_pylist(),
            table.column("_dw_split").to_pylist(),
            strict=True,
        )
    )
    ran = run_operator("label_balancer", {"exclude_columns": EXCL}, table, hp.ROLES)
    counts = _counts(ran.output)
    for split in ("train", "test"):
        assert counts[(split, "humorous")] == counts[(split, "not_humorous")] > 0
    assert counts[("test", "humorous")] < counts[("train", "humorous")]
    for key, split in zip(
        ran.output.column("_dw_row_key").to_pylist(),
        ran.output.column("_dw_split").to_pylist(),
        strict=True,
    ):
        assert before[key] == split


def test_the_cap_and_determinism(data_dir: Path) -> None:
    params = {"exclude_columns": EXCL, "max_per_class": 100}
    a = run_operator("label_balancer", params, hp.labelled_pool(), hp.ROLES, seed=1)
    b = run_operator("label_balancer", params, hp.labelled_pool(), hp.ROLES, seed=1)
    c = run_operator("label_balancer", params, hp.labelled_pool(), hp.ROLES, seed=2)
    keys = lambda r: sorted(r.output.column("_dw_row_key").to_pylist())  # noqa: E731
    assert a.output.num_rows == 200 and keys(a) == keys(b)
    assert keys(c) != keys(a) and c.output.num_rows == 200


def test_no_row_is_ever_duplicated(data_dir: Path) -> None:
    ran = run_operator("label_balancer", {"exclude_columns": EXCL}, hp.labelled_pool(), hp.ROLES)
    pairs = list(
        zip(
            ran.output.column("_dw_row_key").to_pylist(),
            ran.output.column("_dw_occurrence").to_pylist(),
            strict=True,
        )
    )
    assert len(pairs) == len(set(pairs))


@pytest.mark.parametrize(
    ("params", "mutate", "code"),
    [
        ({"label_column": "nope"}, None, "no_label_column"),
        ({}, "null_label", "label_has_nulls"),
        ({}, "one_class", "single_class_label"),
        ({"classes": ["humorous", "not_humorous", "satire"]}, None, "empty_class"),
    ],
)
def test_refusals(data_dir: Path, params: dict, mutate: str | None, code: str) -> None:
    table = hp.labelled_pool().slice(0, 400)
    if mutate == "null_label":
        labels = table.column("label").to_pylist()
        labels[3] = None
        table = table.set_column(
            table.schema.get_field_index("label"), "label", pa.array(labels, pa.string())
        )
    elif mutate == "one_class":
        table = table.filter(pa.array([v == "humorous" for v in table.column("label").to_pylist()]))
    with pytest.raises(StepFailed) as exc:
        run_operator("label_balancer", {"exclude_columns": EXCL, **params}, table, hp.ROLES)
    assert exc.value.code == code


def test_plan_refuses_an_empty_class_in_one_split_and_a_bad_cap() -> None:
    with pytest.raises(LabelBalanceRefusal) as exc:
        label_balance_plan(["a", "b", "a"], ["train", "train", "test"], seed=1)
    assert exc.value.code == "single_class_label" and exc.value.details["split"] == "test"
    with pytest.raises(LabelBalanceRefusal) as exc:
        label_balance_plan(
            ["a", "b", "a", "b"], ["train", "train", "test", "test"], seed=1, classes=["a", "c"]
        )
    assert exc.value.code == "empty_class" and exc.value.details["class"] == "c"
    with pytest.raises(LabelBalanceRefusal) as exc:
        label_balance_plan(["a", "b"], ["x", "x"], seed=1, cap=0)
    assert exc.value.code == "invalid_cap"


def test_the_manifest_has_no_switch_for_the_warning() -> None:
    from src.operators.native.curation.label_balancer import LabelBalancer

    props = LabelBalancer.manifest.params_schema["properties"]
    assert not any("warn" in name for name in props)
    assert "WARNING" in LabelBalancer.manifest.description
