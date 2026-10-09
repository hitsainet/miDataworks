"""Data Designer finaliser: the worker's output to the 002 layout (FR-003.15, FR-003.8, FR-003.9).

Runs on ``labeling`` in the BACKEND image (``midataworks.operators.step.finalize_designer``), linked
after ``midataworks.designer.step`` on success and on failure. The worker wrote, under
``<output_dir>.dd/``, ``output.parquet`` (key columns + the output column, one row per record Data
Designer produced) and ``records.json`` (the relay's per-request records), or ``error.json``.

Rows join back to their input by the carried ``(_dw_row_key, _dw_occurrence)``, never by position.
A row with no record is a ``dropped`` event whose reason is the relay's (``dd.context_overflow``,
``dd.http_400`` …), attributed in request order. Then the kind's effects and conservation are
checked, the 002 layout is published, and the relay records are kept beside it as
``relay_records.json`` for feature 007 (FR-003.26).
"""

from __future__ import annotations

import json
import shutil
import uuid
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from ...core.canonical_json import canonical_json
from ...core.storage import resolve_under_data_dir, staging_dir
from ...services.operator_port import StepSpec
from ...services.step_contract import EVENT_SCHEMA, EVENTS_FILE, META_FILE, part_files
from .. import conservation, effects
from ..context import OccurrenceAllocator, RunContext
from ..errors import OperatorError
from ..executor import _publish, check_manifest_hash, write_failure
from ..protocol import OperatorResult
from .adapter import raw_dir

RELAY_RECORDS_FILE = "relay_records.json"


def build_result(
    batch: pa.Table,
    produced: list[dict[str, Any]],
    output_column: str,
    records: list[dict[str, Any]],
    ctx: RunContext,
) -> OperatorResult:
    """Join produced records to their input rows; a missing record becomes a drop."""
    by_pair = {
        (str(r["_dw_row_key"]), int(r["_dw_occurrence"])): r.get(output_column) for r in produced
    }
    failures = [r for r in records if r.get("reason")]
    keep, values, events = [], [], []
    for index, row in enumerate(batch.select(["_dw_row_key", "_dw_occurrence"]).to_pylist()):
        pair = (row["_dw_row_key"], int(row["_dw_occurrence"]))
        if pair in by_pair:
            keep.append(index)
            values.append(by_pair[pair])
            continue
        reason = failures.pop(0)["reason"] if failures else "not_generated"
        events.append(
            ctx.drop(
                pair,
                f"dd.{reason}",
                f"Data Designer produced no record for this row ({reason})",
                "dd_record",
                0.0,
            )
        )
    # An int64 index array even when empty: `take([])` infers a null type and raises, which made a
    # step where EVERY record failed crash the finaliser instead of dropping every row.
    kept = batch.take(pa.array(keep, pa.int64())).append_column(
        output_column, pa.array(values, pa.string())
    )
    return OperatorResult(output=kept, events=events, output_roles={output_column: "metadata"})


def finalize_step(spec: StepSpec, *, failed: bool = False, registry: Any = None) -> dict[str, Any]:
    from ..registry import current

    registry = registry or current()
    raw = resolve_under_data_dir(raw_dir(spec.output_dir))
    error_file = raw / "error.json"
    if failed or error_file.is_file() or not (raw / "output.parquet").is_file():
        detail = json.loads(error_file.read_text()) if error_file.is_file() else {}
        code = str(detail.get("code") or "designer_failed")
        write_failure(
            spec.output_dir,
            {
                "code": code,
                "message": "Data Designer failed: "
                + str(detail.get("message", "the designer worker reported no output")),
                "details": {"traceback": str(detail.get("traceback", ""))[-4000:]},
            },
        )
        return {"status": "failed", "code": code}
    try:
        entry = registry.require_allowed(spec.operator, spec.version)
        check_manifest_hash(entry, spec.expected_manifest_hash)
        manifest = entry.manifest
        inputs = pa.concat_tables(
            [pq.read_table(p) for p in part_files(resolve_under_data_dir(spec.input_dir))],
            promote_options="default",
        )
        produced = pq.read_table(raw / "output.parquet").to_pylist()
        records = json.loads((raw / "records.json").read_text())
        default = manifest.params_schema["properties"]["output_column"].get("default")
        output_column = str(spec.params.get("output_column") or default)
        ctx = RunContext(
            manifest=manifest,
            manifest_hash=str(entry.manifest_hash),
            step_seed=spec.step_seed,
            job_id=spec.job_id,
            column_roles=dict(spec.column_roles),
            rowkey_scheme=spec.rowkey_scheme,
            step_execution_id=spec.step_execution_id,
            allocator=OccurrenceAllocator(conservation.pairs_of(inputs)),
        )
        result = build_result(inputs, produced, output_column, records, ctx)
        effects.check(manifest.kind, inputs.schema, result, entry.ref)
        content = ctx.content_columns
        accounting = conservation.check(
            conservation.input_rows(inputs, content, spec.rowkey_scheme),
            result.output,
            result.events,
            content,
            spec.rowkey_scheme,
            entry.ref,
        )
    except OperatorError as error:
        write_failure(spec.output_dir, error)
        return {"status": "failed", "code": error.code}
    staged = staging_dir() / f"step-dd-{uuid.uuid4().hex}"
    staged.mkdir(parents=True)
    pq.write_table(result.output, staged / "part-00000.parquet")
    pq.write_table(
        pa.Table.from_pylist([e.file_row() for e in result.events], schema=EVENT_SCHEMA),
        staged / EVENTS_FILE,
    )
    (staged / RELAY_RECORDS_FILE).write_bytes(canonical_json(records))
    roles = dict(spec.column_roles)
    roles.update(result.output_roles or {})
    meta = {
        "rows_in": accounting.rows_in,
        "rows_kept": accounting.rows_kept,
        "rows_changed": accounting.rows_changed,
        "rows_dropped": accounting.rows_dropped,
        "rows_added": accounting.rows_added,
        "rows_split_assigned": accounting.rows_split_assigned,
        "output_column_roles": roles,
        "split_roles": None,
        "operator": entry.ref,
        "manifest_hash": entry.manifest_hash,
        "relay_records": len(records),
        "error": None,
    }
    (staged / META_FILE).write_bytes(canonical_json(meta))
    _publish(staged, resolve_under_data_dir(spec.output_dir))
    shutil.rmtree(raw, ignore_errors=True)
    return {"status": "completed", "rows_in": accounting.rows_in}
