"""Data Designer, backend side: catalogue, dispatch to the designer queue, the key hand-off and the
finaliser (FR-003.15; ADR-010 amendment 2026-10-07). The library itself runs only in the designer
image (designer/tests)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.core.config import get_settings
from src.core.storage import resolve_under_data_dir
from src.operators import conservation, endpoint_port, executor
from src.operators.context import OccurrenceAllocator, RunContext
from src.operators.data_designer import handoff, runner
from src.operators.data_designer.adapter import raw_dir
from src.operators.data_designer.finalize import RELAY_RECORDS_FILE, build_result, finalize_step
from src.operators.endpoint_port import ResolvedEndpoint
from src.operators.errors import OperatorError
from src.operators.registry import DD_CATALOGUE, OperatorRegistry
from src.services.operator_port import StepSpec
from src.services.step_contract import EVENTS_FILE, part_files, read_meta
from tests.support import operator_fixtures as fx
from tests.unit.operators.test_operator_wiring import routed_by

KEY = "sk-NeverInAMessage-0123456789"


def _registry() -> OperatorRegistry:
    return OperatorRegistry.build(
        native=(), catalogues=((DD_CATALOGUE, "data_designer"),), entry_points=()
    )


def test_the_catalogue_operators_load_and_run_on_the_designer_queue() -> None:
    entries = _registry().entries()
    assert {e.name for e, _ in entries} == {"dd_llm_text", "dd_expression"}
    assert {s for _, s in entries} == {"allowed"}
    assert all(
        e.manifest is not None and e.manifest.resources.queue == "designer" for e, _ in entries
    )
    document = json.loads(DD_CATALOGUE.read_text())
    assert all(r["reason"] for r in document["rejected"])


def test_the_backend_never_runs_a_designer_operator_in_process(data_dir: Path) -> None:
    reg = _registry()
    entry = reg.entry("dd_expression", reg.current_version("dd_expression") or "")
    with pytest.raises(OperatorError) as exc:
        reg.implementation(entry)
    assert exc.value.code == "worker_unavailable"


class Resolver:
    def resolve(self, role: str) -> ResolvedEndpoint:
        return ResolvedEndpoint(role, "http://millm.test/v1", "m1", KEY, True)


def _spec(reg: OperatorRegistry, name: str, params: dict[str, Any]) -> StepSpec:
    entry = reg.entry(name, reg.current_version(name) or "")
    return StepSpec(
        step_execution_id="00000000-0000-0000-0000-0000000000ee",
        operator=name,
        version=entry.version,
        params=params,
        input_dir="runs/j/in",
        output_dir="runs/j/steps/s",
        step_seed=1,
        job_id="j",
        bindings=[],
        column_roles=dict(fx.ROLES),
        rowkey_scheme="dw.rowkey/v1",
        expected_manifest_hash=str(entry.manifest_hash),
    )


def test_dispatch_sends_to_the_designer_queue_with_the_key_sealed_not_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[tuple[str, dict[str, Any]]] = []
    sealed: list[tuple[str, str]] = []
    monkeypatch.setattr(executor, "send_task", lambda name, **kw: sent.append((name, kw)))
    monkeypatch.setattr(handoff, "put", lambda ref, key: sealed.append((ref, key)))
    monkeypatch.setattr(endpoint_port, "_resolver", Resolver())
    reg = _registry()
    spec = _spec(reg, "dd_llm_text", {"prompt": "Rewrite: {{ text }}"})
    executor.dispatch_step(reg, spec, "midataworks.versions.advance_build", ["j"])
    assert len(sent) == 1 and len(sealed) == 1
    name, options = sent[0]
    assert name == "midataworks.designer.step"
    payload = options["args"][0]
    assert KEY not in json.dumps(options, default=str), "the key never travels in a message"
    assert payload["key_ref"] == sealed[0][0] and sealed[0][1] == KEY
    assert payload["endpoint"] == {
        "base_url": "http://millm.test/v1",
        "model": "m1",
        "is_millm": True,
    }
    assert payload["output_dir"] == "runs/j/steps/s.dd" and payload["seed_columns"] == ["text"]
    for key, failed in (("link", False), ("link_error", True)):
        assert options[key]["task"] == executor.FINALIZE_DESIGNER
        assert options[key]["kwargs"] == ({"failed": True} if failed else {})
        assert routed_by(runner.app, options[key]) == "labeling"


def test_a_designer_step_without_a_handoff_key_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(endpoint_port, "_resolver", Resolver())
    monkeypatch.setattr(get_settings(), "designer_handoff_key", None)
    monkeypatch.setattr(executor, "send_task", lambda name, **kw: None)
    reg = _registry()
    with pytest.raises(OperatorError) as exc:
        executor.dispatch_step(
            reg,
            _spec(reg, "dd_llm_text", {"prompt": "x"}),
            "midataworks.versions.advance_build",
            ["j"],
        )
    assert exc.value.code == "worker_unavailable" and "DESIGNER_HANDOFF_KEY" in exc.value.message


def test_the_handoff_round_trips_between_backend_and_worker() -> None:
    """Byte compatibility: the backend's seal opens with the worker's code, bound to its ref."""
    sealed = handoff.seal("step:1", KEY, "shared-secret")
    assert KEY.encode() not in sealed
    assert runner.open_sealed("step:1", sealed, "shared-secret") == KEY
    with pytest.raises(Exception):  # noqa: B017 - a different ref or secret must not open it
        runner.open_sealed("step:2", sealed, "shared-secret")
    with pytest.raises(Exception):  # noqa: B017
        runner.open_sealed("step:1", sealed, "other-secret")


def test_handoff_put_stores_the_sealed_key_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import SecretStr

    monkeypatch.setattr(get_settings(), "designer_handoff_key", SecretStr("shared-secret"))
    monkeypatch.setenv("DESIGNER_HANDOFF_KEY", "shared-secret")
    monkeypatch.setenv("REDIS_URL", get_settings().redis_url)
    ref = "test:" + "f" * 16
    handoff.put(ref, KEY)
    with pytest.raises(OperatorError):
        handoff.put(ref, KEY)  # NX: a second put on a waiting ref is refused
    assert runner.take_key(ref) == KEY
    with pytest.raises(RuntimeError, match="already taken"):
        runner.take_key(ref)


def _ctx(data: pa.Table) -> RunContext:
    reg = _registry()
    entry = reg.entry("dd_llm_text", reg.current_version("dd_llm_text") or "")
    return RunContext(
        manifest=entry.manifest,
        manifest_hash=str(entry.manifest_hash),
        step_seed=1,
        job_id=None,
        column_roles=fx.ROLES,
        rowkey_scheme="dw.rowkey/v1",
        allocator=OccurrenceAllocator(conservation.pairs_of(data)),
    )


def test_join_by_carried_key_and_missing_records_become_drops_with_the_relay_reason() -> None:
    """Rows join by (key, occurrence), never by position; a failed record is a drop."""
    data = fx.table()
    rows = data.to_pylist()
    produced = [
        {
            "_dw_row_key": r["_dw_row_key"],
            "_dw_occurrence": r["_dw_occurrence"],
            "llm_text": f"s{i}",
        }
        for i, r in enumerate(rows)
        if i != 3
    ][::-1]
    records = [{"row_key": None, "status": 400, "reason": "context_overflow"}]
    result = build_result(data, produced, "llm_text", records, _ctx(data))
    assert result.output.num_rows == 7
    joined = {
        (r["_dw_row_key"], r["_dw_occurrence"]): r["llm_text"] for r in result.output.to_pylist()
    }
    assert joined[(rows[0]["_dw_row_key"], 0)] == "s0"
    assert [e.reason_code for e in result.events] == ["dd.context_overflow"]
    assert (result.events[0].row_key, result.events[0].occurrence) == (rows[3]["_dw_row_key"], 1)


def _raw(data_dir: Path, spec: StepSpec, data: pa.Table, produced: list[dict[str, Any]]) -> None:
    fx.write_parts(data_dir / spec.input_dir, data)
    raw = data_dir / raw_dir(spec.output_dir)
    raw.mkdir(parents=True)
    pq.write_table(runner._produced_table(produced, "llm_text"), raw / "output.parquet")
    (raw / "records.json").write_text(
        json.dumps([{"row_key": None, "status": 200, "steering_header": "s=1", "reason": None}])
    )


def test_finalize_publishes_the_002_layout_and_keeps_relay_records(data_dir: Path) -> None:
    reg = _registry()
    spec = _spec(reg, "dd_llm_text", {"prompt": "x"})
    data = fx.table(["one", "two"])
    produced = [
        {"_dw_row_key": r["_dw_row_key"], "_dw_occurrence": r["_dw_occurrence"], "llm_text": "ok"}
        for r in data.to_pylist()
    ]
    _raw(data_dir, spec, data, produced)
    assert finalize_step(spec, registry=reg)["status"] == "completed"
    out = resolve_under_data_dir(spec.output_dir)
    meta = read_meta(out)
    assert meta["rows_kept"] == 2 and meta["output_column_roles"]["llm_text"] == "metadata"
    assert pq.read_table(part_files(out)[0]).column("llm_text").to_pylist() == ["ok", "ok"]
    assert pq.read_table(out / EVENTS_FILE).num_rows == 0
    assert json.loads((out / RELAY_RECORDS_FILE).read_text())[0]["steering_header"] == "s=1"


def test_finalize_reports_the_worker_error(data_dir: Path) -> None:
    reg = _registry()
    spec = _spec(reg, "dd_llm_text", {"prompt": "x"})
    raw = data_dir / raw_dir(spec.output_dir)
    raw.mkdir(parents=True)
    (raw / "error.json").write_text(
        json.dumps({"code": "designer_failed", "message": "boom", "traceback": "Traceback"})
    )
    assert finalize_step(spec, registry=reg, failed=True)["code"] == "designer_failed"
    assert read_meta(resolve_under_data_dir(spec.output_dir))["error"]["code"] == "designer_failed"


def test_finalize_refuses_a_lost_row(data_dir: Path) -> None:
    """A produced row naming a pair the input never had is a conservation failure."""
    reg = _registry()
    spec = _spec(reg, "dd_llm_text", {"prompt": "x"})
    data = fx.table(["one"])
    produced = [{"_dw_row_key": "f" * 64, "_dw_occurrence": 0, "llm_text": "stray"}]
    _raw(data_dir, spec, data, produced)
    # the stray record matches no row: the real row is dropped, the stray is ignored
    assert finalize_step(spec, registry=reg)["status"] == "completed"
    assert read_meta(resolve_under_data_dir(spec.output_dir))["rows_dropped"] == 1


def test_a_designer_preview_goes_to_the_designer_worker_without_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.api.v1.endpoints import operators as endpoint
    from src.core.celery_app import celery_app

    sent: list[tuple[str, dict[str, Any]]] = []
    sealed: list[tuple[str, str]] = []
    monkeypatch.setattr(celery_app, "send_task", lambda name, **kw: sent.append((name, kw)))
    monkeypatch.setattr(handoff, "put", lambda ref, key: sealed.append((ref, key)))
    monkeypatch.setattr(endpoint_port, "_resolver", Resolver())
    reg = _registry()
    entry = reg.entry("dd_llm_text", reg.current_version("dd_llm_text") or "")
    request = {
        "operator": "dd_llm_text",
        "version": entry.version,
        "params": {"prompt": "x"},
        "preview_id": "p" * 64,
        "seed": 3,
        "sample_size_effective": 5,
        "resolved_input": {
            "files": ["versions/v/train.parquet"],
            "column_roles": fx.ROLES,
            "rowkey_scheme": "dw.rowkey/v1",
        },
    }
    endpoint.send_preview(request, entry)
    ((name, options),) = sent
    assert name == "midataworks.designer.preview"
    assert options["link"]["task"] == "midataworks.operators.preview"
    assert options["link"]["args"][0]["designer"] is True
    assert KEY not in json.dumps(options, default=str) and sealed[0][1] == KEY
    assert options["args"][0]["files"] == ["versions/v/train.parquet"]
