"""``cell_balancer``, ``metadata_value_filter`` and ``split`` through 003's real executor, with the
prototype's goldens (FTASKS 3.3, 6.2–6.5, 7.2, 7.4, 7.5)."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pyarrow as pa
import pytest

from src.operators.errors import StepFailed
from tests.fixtures import humor_pool as hp
from tests.support.curation_fixtures import run_operator

EXCL = ["label_probability"]


def _cells(table: pa.Table, a: str, b: str = "label") -> dict[str, int]:
    return dict(
        Counter(
            f"{x}|{y}"
            for x, y in zip(table.column(a).to_pylist(), table.column(b).to_pylist(), strict=True)
        )
    )


@pytest.fixture
def no_news(data_dir: Path) -> pa.Table:
    ran = run_operator(
        "metadata_value_filter",
        {"column": "source_label", "values": ["news"]},
        hp.labelled_pool(),
        hp.ROLES,
    )
    news = ran.events_by_reason("value_excluded")
    assert len(news) == 1390 and news[0]["statistic_name"] == "source_label"
    assert news[0]["statistic_text"] == "source_label=news"
    return ran.output


def test_cell_balancer_reproduces_613(no_news: pa.Table) -> None:
    sink: list[dict] = []
    ran = run_operator(
        "cell_balancer",
        {"column": "format", "exclude_columns": EXCL},
        no_news,
        hp.ROLES,
        report_sink=sink,
    )
    assert ran.output.num_rows == 4 * 613 == 2452
    assert set(_cells(ran.output, "format").values()) == {613}
    report = sink[0]
    assert report["cap"] == 613 and report["rows_kept"] == 2452
    caps = ran.events_by_reason("cell_cap")
    assert len(caps) == no_news.num_rows - 2452
    assert caps[0]["statistic_name"] == "cell_size"
    assert '"comparator":"sampled_to"' in caps[0]["threshold"]
    # re-audit (FR-004.40): the balanced column now reads at or below chance, and does not warn
    by = {c["column"]: c for c in report["reaudit"]["columns"]}
    assert by["format"]["figure"] <= 0.500 + 1e-9
    from src.services.curation.api import evaluate_audit
    from src.services.curation.level_service import EffectiveLevel

    warnings, _, _ = evaluate_audit(
        "v", report["reaudit"], EffectiveLevel(10, "code_default", None, None, None)
    )
    assert "format" not in {w.column for w in warnings}
    assert report["stratify_default"] == ["label", "format"]


def test_balancer_flags_reddit_news_on_another_column(data_dir: Path) -> None:
    sink: list[dict] = []
    run_operator(
        "cell_balancer",
        {"column": "format", "exclude_columns": EXCL},
        hp.labelled_pool(),
        hp.ROLES,
        report_sink=sink,
    )
    flags = {(f["column"], f["value"]): f for f in sink[0]["extreme_values"]}
    assert flags[("source_label", "news")]["dominant_rows"] == 1387
    assert flags[("source_label", "news")]["rows"] == 1390


def test_balancer_excludes_values_of_its_own_column(data_dir: Path) -> None:
    ran = run_operator(
        "cell_balancer",
        {"column": "source_label", "exclude_values": ["news"], "exclude_columns": EXCL},
        hp.labelled_pool(),
        hp.ROLES,
    )
    assert len(ran.events_by_reason("value_excluded")) == 1390
    assert set(_cells(ran.output, "source_label").values()) == {34}  # joke_clean|not_humorous


@pytest.mark.parametrize(
    ("params", "code"),
    [
        ({"column": "label"}, "invalid_balance_column"),
        ({"column": "text"}, "invalid_balance_column"),
        ({"column": "label_probability", "exclude_columns": EXCL}, "invalid_balance_column"),
        ({"column": "source_label"}, "empty_cell"),  # news|humorous exists, but see below
    ],
)
def test_balancer_refusals(data_dir: Path, params: dict, code: str) -> None:
    table = hp.labelled_pool()
    if code == "empty_cell":
        keep = [
            s != "news" or lab != "humorous"
            for s, lab in zip(
                table.column("source_label").to_pylist(),
                table.column("label").to_pylist(),
                strict=True,
            )
        ]
        table = table.filter(pa.array(keep))
    with pytest.raises(StepFailed) as exc:
        run_operator("cell_balancer", params, table, hp.ROLES)
    assert exc.value.code == code
    if code == "empty_cell":
        assert exc.value.details["value"] == "news"


def test_balancer_refuses_one_class(data_dir: Path) -> None:
    table = hp.candidates()
    table = table.set_column(
        table.schema.get_field_index("label"), "label", pa.array(["x"] * table.num_rows)
    )
    with pytest.raises(StepFailed) as exc:
        run_operator("cell_balancer", {"column": "format"}, table, hp.ROLES)
    assert exc.value.code == "single_class_label"


SPLIT = {"split_names": ["train", "test"], "split_fractions": [0.9, 0.1], "held_out": ["test"]}


def test_split_reproduces_publish_prep(data_dir: Path) -> None:
    sink: list[dict] = []
    ran = run_operator(
        "split",
        {**SPLIT, "stratify_by": ["source_label", "label"]},
        hp.candidates(),
        hp.ROLES,
        report_sink=sink,
    )
    splits = Counter(ran.output.column("_dw_split").to_pylist())
    assert splits == {"train": hp.PUBLISH_TRAIN, "test": hp.PUBLISH_TEST}
    test = ran.output.filter(
        pa.array([s == "test" for s in ran.output.column("_dw_split").to_pylist()])
    )
    assert _cells(test, "source_label") == hp.PUBLISH_TEST_CELLS
    assert ran.meta["split_roles"]["test"]["held_out"] is True
    assert ran.meta["split_roles"]["train"]["held_out"] is False
    assigned = ran.events_by_reason("split_assigned")
    assert len(assigned) == hp.PUBLISH_TEST  # every row started in train; only test rows moved
    assert {e["split"] for e in assigned} == {"test"}
    assert ran.result.rows_dropped == 0 and ran.result.rows_changed == 0


def test_split_balanced_61_per_cell(no_news: pa.Table) -> None:
    balanced = run_operator(
        "cell_balancer", {"column": "format", "exclude_columns": EXCL}, no_news, hp.ROLES
    ).output
    ran = run_operator("split", {**SPLIT, "stratify_by": ["label", "format"]}, balanced, hp.ROLES)
    by = Counter(
        f"{s}|{f}|{lab}"
        for s, f, lab in zip(
            ran.output.column("_dw_split").to_pylist(),
            ran.output.column("format").to_pylist(),
            ran.output.column("label").to_pylist(),
            strict=True,
        )
    )
    assert {k: v for k, v in by.items() if k.startswith("test")} == {
        f"test|{f}|{lab}": 61 for f in ("joke", "headline") for lab in ("humorous", "not_humorous")
    }
    assert sum(v for k, v in by.items() if k.startswith("train")) == hp.FORMAT_TRAIN


def test_split_keep_source_preserves_upstream_splits(data_dir: Path) -> None:
    rows = [
        {"text": f"edit {i}", "label": "a" if i % 2 else "b", "pair_id": i // 3} for i in range(30)
    ]
    table = hp.with_system_columns(rows)
    upstream = ["train"] * 20 + ["validation"] * 5 + ["test"] * 5
    table = table.set_column(
        table.schema.get_field_index("_dw_split"), "_dw_split", pa.array(upstream)
    )
    ran = run_operator(
        "split",
        {
            "split_names": ["train", "validation", "test"],
            "held_out": ["test"],
            "mode": "keep_source",
        },
        table,
        {"text": "content", "label": "metadata", "pair_id": "metadata"},
    )
    assert sorted(ran.output.column("_dw_split").to_pylist()) == sorted(upstream)
    assert ran.events.num_rows == 0
    assert ran.meta["split_roles"]["test"]["held_out"] is True


def test_split_refuses_generated_rows_in_held_out(data_dir: Path) -> None:
    table = hp.candidates().slice(0, 200)
    origin = ["generated"] * 200
    table = table.set_column(
        table.schema.get_field_index("_dw_origin"), "_dw_origin", pa.array(origin)
    )
    with pytest.raises(StepFailed) as exc:
        run_operator("split", {**SPLIT, "stratify_by": ["label"]}, table, hp.ROLES)
    assert exc.value.code == "generated_in_held_out"


def test_split_keeps_groups_whole(data_dir: Path) -> None:
    rows = [
        {"text": f"headline {i}", "label": "a" if i % 2 else "b", "pair_id": f"p{i // 2}"}
        for i in range(200)
    ]
    table = hp.with_system_columns(rows)
    ran = run_operator(
        "split",
        {**SPLIT, "group_column": "pair_id"},
        table,
        {"text": "content", "label": "metadata", "pair_id": "metadata"},
    )
    seen: dict[str, set[str]] = {}
    for p, s in zip(
        ran.output.column("pair_id").to_pylist(),
        ran.output.column("_dw_split").to_pylist(),
        strict=True,
    ):
        seen.setdefault(p, set()).add(s)
    assert all(len(v) == 1 for v in seen.values())


@pytest.mark.parametrize(
    "params",
    [
        {"split_names": ["train", "test"], "split_fractions": [0.8, 0.1]},
        {"split_names": ["train", "test"], "split_fractions": [0.9]},
        {"split_names": ["train", "test"], "split_fractions": [0.9, 0.1], "held_out": ["dev"]},
    ],
)
def test_split_fraction_refusals(data_dir: Path, params: dict) -> None:
    with pytest.raises(StepFailed) as exc:
        run_operator("split", params, hp.candidates().slice(0, 50), hp.ROLES)
    assert exc.value.code == "split_fractions_invalid"


def test_operators_are_deterministic_for_one_seed(data_dir: Path) -> None:
    table = hp.labelled_pool()
    params = {"column": "format", "exclude_values": [], "exclude_columns": EXCL}
    a = run_operator(
        "cell_balancer",
        {**params},
        table.filter(pa.array([s != "news" for s in table.column("source_label").to_pylist()])),
        hp.ROLES,
        seed=3,
    )
    b = run_operator(
        "cell_balancer",
        {**params},
        table.filter(pa.array([s != "news" for s in table.column("source_label").to_pylist()])),
        hp.ROLES,
        seed=3,
    )
    assert a.output.column("_dw_row_key").to_pylist() == b.output.column("_dw_row_key").to_pylist()
    assert a.events.to_pylist() == b.events.to_pylist()
