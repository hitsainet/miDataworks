"""Kind effects, events and conservation (FR-003.5, FR-003.8, FR-003.9; FTASKS 3.1–3.6)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.core.storage import resolve_under_data_dir
from src.operators import conservation, effects
from src.operators.errors import OperatorError
from src.operators.executor import execute_in_process
from src.operators.protocol import OperatorResult, RowEvent
from src.services.row_keys import ROWKEY_V1
from src.services.step_contract import EVENTS_FILE, META_FILE, part_files, read_meta
from tests.support import operator_fixtures as fx


def _ev(kind: str, key: str, occ: int = 0, **over: Any) -> RowEvent:
    values: dict[str, Any] = {
        "kind": kind,
        "row_key": key,
        "occurrence": occ,
        "reason_code": "r",
        "reason": "why",
        "operator_name": "t",
        "operator_version": "1",
        "manifest_hash": "h",
        "statistic_name": "s" if kind in {"dropped", "changed"} else None,
    }
    values.update(over)
    return RowEvent(**values)


# --- 3.1 kind effects -------------------------------------------------------------------------


def _result(output: pa.Table, events: list[RowEvent] | None = None, **kw: Any) -> OperatorResult:
    return OperatorResult(output=output, events=events or [], **kw)


def test_permitted_effects_pass_per_kind() -> None:
    data = fx.table()
    key = data.column("_dw_row_key")[0].as_py()
    drop = _result(data.slice(1), [_ev("dropped", key)])
    for kind in ("filter", "deduplicator", "selector", "labeler"):
        assert effects.check(kind, data.schema, drop, "x@1") == {"drop"}
    labelled = _result(data.append_column("label", pa.array([1.0] * data.num_rows)))
    assert effects.check("labeler", data.schema, labelled, "x@1") == {"add_columns"}
    split = _result(data, [_ev("split_assigned", key, split="test")])
    assert effects.check("selector", data.schema, split, "x@1") == {"assign_split"}
    added = _result(data, added=data.slice(0, 1))
    assert effects.check("generator", data.schema, added, "x@1") == {"add_rows"}
    assert effects.check("report", data.schema, _result(data), "x@1") == set()


@pytest.mark.parametrize(
    ("kind", "event_kind"),
    [("filter", "changed"), ("mapper", "added"), ("report", "changed"), ("generator", "dropped")],
)
def test_forbidden_effect_raises_kind_effect_violated(kind: str, event_kind: str) -> None:
    data = fx.table()
    key = data.column("_dw_row_key")[0].as_py()
    extra = {"new_row_key": key} if event_kind == "changed" else {}
    result = _result(data, [_ev(event_kind, key, **extra)])
    with pytest.raises(OperatorError) as exc:
        effects.check(kind, data.schema, result, "x@1")
    assert exc.value.code == "kind_effect_violated"


def test_report_that_adds_a_column_is_refused() -> None:
    data = fx.table()
    result = _result(data.append_column("x", pa.array([1] * data.num_rows)))
    with pytest.raises(OperatorError):
        effects.check("report", data.schema, result, "x@1")


def test_selector_allows_assign_split_and_filter_does_not() -> None:
    """FR-003.27: assign_split is in the selector's set, and only there."""
    assert "assign_split" in effects.ALLOWED["selector"]
    assert all("assign_split" not in v for k, v in effects.ALLOWED.items() if k != "selector")


# --- 3.2 conservation -------------------------------------------------------------------------


def _check(data: pa.Table, output: pa.Table, events: list[RowEvent]) -> conservation.Accounting:
    inputs = conservation.input_rows(data, ["text"], ROWKEY_V1)
    return conservation.check(inputs, output, events, ["text"], ROWKEY_V1, "t@1")


def _problems(data: pa.Table, output: pa.Table, events: list[RowEvent]) -> dict[str, Any]:
    with pytest.raises(OperatorError) as exc:
        _check(data, output, events)
    assert exc.value.code == "conservation_violated"
    return exc.value.details


def test_kept_and_dropped_with_events_reconcile() -> None:
    data = fx.table()
    key = data.column("_dw_row_key")[0].as_py()
    accounting = _check(data, data.slice(1), [_ev("dropped", key)])
    assert (accounting.rows_in, accounting.rows_kept, accounting.rows_dropped) == (8, 7, 1)


def test_lost_row_is_named() -> None:
    data = fx.table()
    details = _problems(data, data.slice(1), [])
    assert details["lost"][0]["row_key"] == data.column("_dw_row_key")[0].as_py()


def test_duplicated_row_is_named() -> None:
    data = fx.table()
    details = _problems(data, pa.concat_tables([data, data.slice(0, 1)]), [])
    assert "duplicated_in_output" in details


def test_dropped_and_kept_at_once_is_named() -> None:
    data = fx.table()
    key = data.column("_dw_row_key")[0].as_py()
    details = _problems(data, data, [_ev("dropped", key)])
    assert details["dropped_and_kept"][0]["row_key"] == key


def test_changed_content_without_changed_event_is_named() -> None:
    data = fx.table()
    text = pa.array([t.upper() for t in data.column("text").to_pylist()])
    mutated = data.set_column(0, "text", text)
    details = _problems(data, mutated, [])
    assert len(details["changed_without_event"]) == 7  # all but the whitespace row


def test_metadata_change_without_event_is_not_a_content_change() -> None:
    data = fx.table()
    retagged = data.set_column(1, "note", pa.array(["z"] * data.num_rows))
    assert _check(data, retagged, []).rows_kept == 8


def test_split_change_without_event_is_named_and_with_event_passes() -> None:
    data = fx.table()
    moved = data.set_column(4, "_dw_split", pa.array(["test"] * data.num_rows))
    details = _problems(data, moved, [])
    assert len(details["split_changed_without_event"]) == 8
    events = [
        _ev("split_assigned", r["_dw_row_key"], r["_dw_occurrence"], split="test")
        for r in data.to_pylist()
    ]
    assert _check(data, moved, events).rows_split_assigned == 8


def test_problem_lists_are_capped_at_twenty() -> None:
    data = fx.table([f"row number {i}" for i in range(30)])
    details = _problems(data, data.slice(0, 0), [])
    assert len(details["lost"]) == 20


def test_generators_added_rows_are_counted_separately() -> None:
    data = fx.table(["parent row"])
    new = fx.table(["child row"])
    child = new.column("_dw_row_key")[0].as_py()
    accounting = _check(data, pa.concat_tables([data, new]), [_ev("added", child)])
    assert (accounting.rows_kept, accounting.rows_added) == (1, 1)
    details = _problems(data, pa.concat_tables([data, new]), [])
    assert "unaccounted_output" in details


def test_duplicate_key_in_two_rows_conserves_by_pair() -> None:
    """FR-002.47: the duplicated text has occurrences 0 and 1; dropping one keeps the other."""
    data = fx.table()
    dup = [r for r in data.to_pylist() if r["_dw_occurrence"] == 1][0]
    output = pa.Table.from_pylist(
        [r for r in data.to_pylist() if r["_dw_occurrence"] == 0], schema=data.schema
    )
    accounting = _check(data, output, [_ev("dropped", dup["_dw_row_key"], 1)])
    assert accounting.rows_dropped == 1 and accounting.rows_kept == 7


# --- through the executor (3.3–3.6) ---------------------------------------------------------


@pytest.fixture
def reg() -> Any:
    return fx.registry()


def test_drop_short_end_to_end_writes_the_002_layout(data_dir: Path, reg: Any) -> None:
    spec = fx.stage_spec(reg, "fx_drop_short", {"min_len": 10}, data=fx.table())
    result = execute_in_process(spec, registry=reg)
    out = resolve_under_data_dir(spec.output_dir)
    meta = read_meta(out)
    assert (meta["rows_in"], meta["rows_dropped"], meta["rows_kept"]) == (8, 3, 5)
    assert result.rows_dropped == 3
    events = pq.read_table(out / EVENTS_FILE).to_pylist()
    assert {e["reason_code"] for e in events} == {"too_short"}
    assert all(e["statistic_name"] == "text_length" for e in events)
    kept = pq.read_table(part_files(out)[0])
    assert kept.num_rows == 5


def test_drop_without_reason_fails_the_step_and_publishes_nothing(data_dir: Path, reg: Any) -> None:
    """3.4: the refusal is at event construction."""
    spec = fx.stage_spec(reg, "fx_drop_no_reason", data=fx.table())
    with pytest.raises(OperatorError) as exc:
        execute_in_process(spec, registry=reg)
    assert exc.value.code == "event_invalid"
    assert not resolve_under_data_dir(spec.output_dir).exists()


def test_non_reconciling_count_publishes_nothing(data_dir: Path, reg: Any) -> None:
    """3.5: fx_lose_row; no output is renamed into place."""
    spec = fx.stage_spec(reg, "fx_lose_row", data=fx.table())
    with pytest.raises(OperatorError) as exc:
        execute_in_process(spec, registry=reg)
    assert exc.value.code == "conservation_violated"
    assert exc.value.details["lost"]
    assert not resolve_under_data_dir(spec.output_dir).exists()


def test_silent_mutation_is_caught_by_the_executor(data_dir: Path, reg: Any) -> None:
    spec = fx.stage_spec(reg, "fx_mutate_silently", data=fx.table())
    with pytest.raises(OperatorError) as exc:
        execute_in_process(spec, registry=reg)
    assert "changed_without_event" in exc.value.details


def test_filter_that_changes_is_refused_by_the_executor(data_dir: Path, reg: Any) -> None:
    spec = fx.stage_spec(reg, "fx_filter_changes", data=fx.table())
    with pytest.raises(OperatorError) as exc:
        execute_in_process(spec, registry=reg)
    assert exc.value.code == "kind_effect_violated"


def test_empty_batch_is_valid_and_produces_nothing(data_dir: Path, reg: Any) -> None:
    """3.6: valid input, empty output, zero events."""
    spec = fx.stage_spec(reg, "fx_drop_short", {"min_len": 3}, data=fx.table([]))
    execute_in_process(spec, registry=reg)
    out = resolve_under_data_dir(spec.output_dir)
    meta = read_meta(out)
    assert (meta["rows_in"], meta["rows_kept"], meta["rows_dropped"]) == (0, 0, 0)
    assert pq.read_table(out / EVENTS_FILE).num_rows == 0
    assert pq.read_table(part_files(out)[0]).num_rows == 0
    assert (out / META_FILE).is_file()


def test_mapper_changes_carry_new_keys_and_fresh_occurrences(data_dir: Path, reg: Any) -> None:
    """3.3: ctx.change fills identity; the duplicate pair maps to distinct new pairs."""
    spec = fx.stage_spec(reg, "fx_mapper_upper", data=fx.table())
    execute_in_process(spec, registry=reg)
    out = resolve_under_data_dir(spec.output_dir)
    rows = pq.read_table(part_files(out)[0]).to_pylist()
    pairs = [(r["_dw_row_key"], r["_dw_occurrence"]) for r in rows]
    assert len(pairs) == len(set(pairs)) == 8
    events = pq.read_table(out / EVENTS_FILE).to_pylist()
    assert len(events) == 7 and all(e["new_row_key"] for e in events)


def test_dedup_names_the_kept_key(data_dir: Path, reg: Any) -> None:
    """3.3 / 6.3: a dataset-scope deduplicator over two parts."""
    data = fx.table()
    fx.write_parts(data_dir / "in", data, parts=2)
    spec = fx.stage_spec(reg, "fx_dedup_exact", input_dir="in")
    execute_in_process(spec, registry=reg)
    events = pq.read_table(resolve_under_data_dir(spec.output_dir) / EVENTS_FILE).to_pylist()
    assert len(events) == 1
    dup = [r for r in data.to_pylist() if r["_dw_occurrence"] == 1][0]
    assert events[0]["row_key"] == dup["_dw_row_key"] and events[0]["occurrence"] == 1
    assert events[0]["related_row_key"] == dup["_dw_row_key"]
