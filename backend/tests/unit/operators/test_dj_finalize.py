"""The Data-Juicer finaliser on recorded raw decisions (FR-003.14, FR-003.8, FR-003.9; FTASKS 7.8)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.core.storage import resolve_under_data_dir
from src.operators.datajuicer.adapter import raw_dir, runner_payload
from src.operators.datajuicer.finalize import finalize_step
from src.operators.registry import DJ_CATALOGUE, OperatorRegistry
from src.services.operator_port import StepSpec
from src.services.step_contract import EVENTS_FILE, part_files, read_meta

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "operators"


@pytest.fixture
def reg() -> OperatorRegistry:
    return OperatorRegistry.build(
        native=(), catalogues=((DJ_CATALOGUE, "datajuicer"),), entry_points=()
    )


def _spec(reg: OperatorRegistry, name: str, params: dict[str, Any]) -> StepSpec:
    entry = reg.entry(name, reg.current_version(name) or "?")
    return StepSpec(
        step_execution_id="00000000-0000-0000-0000-0000000000dd",
        operator=name,
        version=entry.version,
        params=params,
        input_dir="runs/j/in",
        output_dir="runs/j/steps/s",
        step_seed=1,
        job_id="j",
        bindings=[],
        column_roles={"text": "content", "note": "metadata"},
        rowkey_scheme="dw.rowkey/v1",
        expected_manifest_hash=str(entry.manifest_hash),
    )


def _raw(data_dir: Path, spec: StepSpec, op_name: str) -> pa.Table:
    """Lay out the runner's recorded answer for ``op_name`` from the committed expectations."""
    fixture = pq.read_table(FIXTURES / "contract_fixture.parquet")
    (data_dir / spec.input_dir).mkdir(parents=True)
    pq.write_table(fixture, data_dir / spec.input_dir / "part-00000.parquet")
    expected = json.loads((FIXTURES / "expectations" / f"{op_name}.json").read_text())
    drops = {(d["row_key"], d["occurrence"]): d for d in expected["dropped"]}
    rows = fixture.to_pylist()
    decisions = [
        {
            "row_key": r["_dw_row_key"],
            "occurrence": r["_dw_occurrence"],
            "keep": (r["_dw_row_key"], r["_dw_occurrence"]) not in drops,
            "stats_json": json.dumps(
                drops.get((r["_dw_row_key"], r["_dw_occurrence"]), {}).get("stats") or {}
            ),
            "kept_key": drops.get((r["_dw_row_key"], r["_dw_occurrence"]), {}).get("kept_key"),
        }
        for r in rows
    ]
    kept = [r for r in rows if (r["_dw_row_key"], r["_dw_occurrence"]) not in drops]
    raw = data_dir / raw_dir(spec)
    raw.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(kept, schema=fixture.schema), raw / "output.parquet")
    pq.write_table(pa.Table.from_pylist(decisions), raw / "decisions.parquet")
    return fixture


def test_filter_drops_become_events_with_the_statistic(
    data_dir: Path, reg: OperatorRegistry
) -> None:
    spec = _spec(reg, "dj_text_length_filter", {"min_len": 20, "max_len": 120})
    _raw(data_dir, spec, "text_length_filter")
    assert finalize_step(spec, registry=reg)["status"] == "completed"
    out = resolve_under_data_dir(spec.output_dir)
    events = pq.read_table(out / EVENTS_FILE).to_pylist()
    assert len(events) == 4
    assert {e["reason_code"] for e in events} == {"dj.text_length_filter"}
    assert all(e["statistic_name"] == "text_len" for e in events)
    assert sorted(e["statistic_value"] for e in events)[0] == 2.0
    assert json.loads(events[0]["threshold"])["comparator"] == "within"
    assert read_meta(out)["rows_dropped"] == 4
    assert pq.read_table(part_files(out)[0]).num_rows == 12
    assert not resolve_under_data_dir(raw_dir(spec)).exists(), "raw output is cleaned up"


def test_dedup_drop_names_the_kept_row(data_dir: Path, reg: OperatorRegistry) -> None:
    spec = _spec(reg, "dj_document_deduplicator", {})
    _raw(data_dir, spec, "document_deduplicator")
    finalize_step(spec, registry=reg)
    events = pq.read_table(resolve_under_data_dir(spec.output_dir) / EVENTS_FILE).to_pylist()
    assert len(events) == 1 and events[0]["related_row_key"] == events[0]["row_key"]
    assert events[0]["occurrence"] == 1


def test_a_lost_row_in_the_runner_output_is_a_conservation_failure(
    data_dir: Path, reg: OperatorRegistry
) -> None:
    spec = _spec(reg, "dj_text_length_filter", {"min_len": 20, "max_len": 120})
    _raw(data_dir, spec, "text_length_filter")
    output = data_dir / raw_dir(spec) / "output.parquet"
    pq.write_table(pq.read_table(output).slice(1), output)
    assert finalize_step(spec, registry=reg)["code"] == "conservation_violated"
    meta = read_meta(resolve_under_data_dir(spec.output_dir))
    assert meta["error"]["code"] == "conservation_violated"
    assert not part_files(resolve_under_data_dir(spec.output_dir))


def test_a_drop_without_its_statistic_fails(data_dir: Path, reg: OperatorRegistry) -> None:
    spec = _spec(reg, "dj_text_length_filter", {"min_len": 20, "max_len": 120})
    _raw(data_dir, spec, "text_length_filter")
    decisions = data_dir / raw_dir(spec) / "decisions.parquet"
    table = pq.read_table(decisions).to_pylist()
    for row in table:
        row["stats_json"] = "{}"
    pq.write_table(pa.Table.from_pylist(table), decisions)
    assert finalize_step(spec, registry=reg)["code"] == "event_invalid"


def test_runner_error_json_reaches_the_build_with_the_traceback(
    data_dir: Path, reg: OperatorRegistry
) -> None:
    """FTASKS 7.6: the error link reports Data-Juicer's traceback."""
    spec = _spec(reg, "dj_text_length_filter", {"min_len": 20})
    raw = data_dir / raw_dir(spec)
    raw.mkdir(parents=True)
    (raw / "error.json").write_text(json.dumps({"message": "boom", "traceback": "Traceback ..."}))
    assert finalize_step(spec, registry=reg, failed=True)["status"] == "failed"
    error = read_meta(resolve_under_data_dir(spec.output_dir))["error"]
    assert error["code"] == "datajuicer_failed" and "boom" in error["message"]
    assert "Traceback" in error["details"]["traceback"]


def test_mapper_changes_are_rekeyed_with_002s_function(
    data_dir: Path, reg: OperatorRegistry
) -> None:
    from src.operators import conservation
    from src.operators.context import OccurrenceAllocator, RunContext
    from src.operators.datajuicer.finalize import build_events

    fixture = pq.read_table(FIXTURES / "contract_fixture.parquet")
    entry = reg.entry("dj_text_length_filter", reg.current_version("dj_text_length_filter") or "")
    ctx = RunContext(
        manifest=entry.manifest,
        manifest_hash=str(entry.manifest_hash),
        step_seed=1,
        job_id=None,
        column_roles={"text": "content"},
        rowkey_scheme="dw.rowkey/v1",
        allocator=OccurrenceAllocator(conservation.pairs_of(fixture)),
    )
    mapped = fixture.set_column(
        0, "text", pa.array([t.strip().lower() for t in fixture.column("text").to_pylist()])
    )
    output, events = build_events(
        ctx, "mapper", "clean", None, {}, {}, fixture, mapped, pa.table({})
    )
    assert events and all(e.kind == "changed" for e in events)
    checked = conservation.check(
        conservation.input_rows(fixture, ["text"], "dw.rowkey/v1"),
        output,
        events,
        ["text"],
        "dw.rowkey/v1",
        "x",
    )
    assert checked.rows_changed == len(events)


def test_runner_payload_is_plain_json(reg: OperatorRegistry) -> None:
    spec = _spec(reg, "dj_text_length_filter", {"min_len": 3})
    payload = runner_payload(reg.entry(spec.operator, spec.version), spec)
    assert payload["op_name"] == "text_length_filter" and payload["stats_key"] == "text_len"
    assert payload["output_dir"] == "runs/j/steps/s.dj"
    json.dumps(payload)
