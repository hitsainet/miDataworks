"""Data-Juicer finaliser: raw decisions to events (FR-003.14, FR-003.8, FR-003.9; FTID 003 section 3.8).

Runs on ``curation`` in the BACKEND image (``midataworks.operators.step.finalize_datajuicer``),
linked after the runner's ``midataworks.datajuicer.step``. The runner, which has no database and no
``src``, wrote under ``<output_dir>.dj/``:

- ``output.parquet`` — the rows Data-Juicer kept (mapped, for a mapper), ``_dw_`` columns carried;
- ``decisions.parquet`` — one row per input row: ``row_key``, ``occurrence``, ``keep``,
  ``stats_json`` (the row's ``__dj__stats__``) and ``kept_key`` (a deduplicator's survivor);
- ``error.json`` — instead of the two, when the runner failed (message and traceback).

Here each drop becomes a ``dropped`` event with reason code ``dj.<op>`` and the statistic the
catalogue names (a filter whose statistic cannot be recovered is never allowlisted); a mapper's
changed rows are re-keyed with feature 002's row-key function and get ``changed`` events; then
conservation runs and the 002 layout is published. A failure publishes only an error ``meta.json``.
"""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from ...core.canonical_json import canonical_json
from ...core.storage import resolve_under_data_dir, staging_dir
from ...services.operator_port import StepSpec
from ...services.step_contract import EVENT_SCHEMA, EVENTS_FILE, META_FILE, part_files
from .. import conservation
from ..context import OccurrenceAllocator, RunContext
from ..errors import OperatorError, StepFailed
from ..executor import _conform, _publish, check_manifest_hash, write_failure
from ..protocol import RowEvent
from .adapter import raw_dir

logger = logging.getLogger(__name__)

DECISION_COLUMNS = ("row_key", "occurrence", "keep", "stats_json", "kept_key")


def _comparison(params: dict[str, Any], threshold_params: dict[str, str]) -> tuple[Any, str]:
    """The threshold value and comparator a filter applied, from its catalogue map."""
    present = {p: params[p] for p in threshold_params if p in params}
    if len(present) == 1:
        ((param, value),) = present.items()
        return value, threshold_params[param]
    if present:
        return present, "within"
    return None, "?"


def build_events(
    ctx: RunContext,
    kind: str,
    op_name: str,
    stats_key: str | None,
    threshold_params: dict[str, str],
    params: dict[str, Any],
    inputs: pa.Table,
    output: pa.Table,
    decisions: pa.Table,
) -> tuple[pa.Table, list[RowEvent]]:
    """Events (and, for a mapper, re-keyed output) from one runner result."""
    code = f"dj.{op_name}"
    events: list[RowEvent] = []
    if kind in {"filter", "deduplicator"}:
        value, comparator = _comparison(params, threshold_params)
        for row in decisions.to_pylist():
            if row["keep"]:
                continue
            pair = (row["row_key"], int(row["occurrence"]))
            if kind == "deduplicator":
                kept = row.get("kept_key")
                events.append(
                    ctx.drop(
                        pair,
                        code,
                        f"duplicate of {str(kept)[:12]}" if kept else "duplicate",
                        "duplicate_of",
                        1.0,
                        kept_key=kept,
                    )
                )
                continue
            stats = json.loads(row["stats_json"] or "{}")
            if stats_key is None or stats_key not in stats:
                raise StepFailed(
                    "event_invalid",
                    f"Data-Juicer {op_name} dropped row {pair[0][:12]} without its statistic "
                    f"{stats_key!r}; the runner's decisions are incomplete.",
                    {"row_key": pair[0], "occurrence": pair[1]},
                )
            observed = stats[stats_key]
            numeric = float(observed) if isinstance(observed, int | float) else None
            events.append(
                ctx.drop(
                    pair,
                    code,
                    f"{stats_key} {observed} failed {op_name} ({comparator} {value})",
                    stats_key,
                    numeric,
                    value,
                    comparator,
                    text=None if numeric is not None else str(observed),
                )
            )
        return output, events
    if kind == "selector":
        kept_pairs = set(conservation.pairs_of(output))
        for pair in conservation.pairs_of(inputs):
            if pair not in kept_pairs:
                events.append(ctx.drop(pair, code, f"not selected by {op_name}", "selected", 0.0))
        return output, events
    if kind == "mapper":
        before = conservation.input_rows(inputs, ctx.content_columns, ctx.rowkey_scheme)
        rows = output.to_pylist()
        for row in rows:
            pair = (row["_dw_row_key"], int(row["_dw_occurrence"]))
            new_key = ctx.row_key(row)
            known = before.get(pair)
            if known is None or known.content_key == new_key:
                continue
            event = ctx.change(pair, new_key, code, f"rewritten by {op_name}", "content", None)
            row["_dw_row_key"], row["_dw_occurrence"] = event.new_row_key, event.new_occurrence
            events.append(event)
        return pa.Table.from_pylist(rows, schema=output.schema), events
    raise StepFailed("kind_effect_violated", f"Data-Juicer kind {kind!r} is not supported.")


def finalize_step(spec: StepSpec, *, failed: bool = False, registry: Any = None) -> dict[str, Any]:
    """Finalise one Data-Juicer step and publish the 002 layout (or an error meta.json)."""
    from ..registry import current

    registry = registry or current()
    raw = resolve_under_data_dir(raw_dir(spec))
    error_file = raw / "error.json"
    if failed or error_file.is_file():
        detail = json.loads(error_file.read_text()) if error_file.is_file() else {}
        write_failure(
            spec.output_dir,
            {
                "code": str(detail.get("code") or "datajuicer_failed"),
                "message": "Data-Juicer failed: "
                + str(detail.get("message", "the runner reported no detail")),
                "details": {"traceback": str(detail.get("traceback", ""))[-4000:]},
            },
        )
        return {"status": "failed", "code": str(detail.get("code") or "datajuicer_failed")}
    try:
        entry = registry.require_allowed(spec.operator, spec.version)
        check_manifest_hash(entry, spec.expected_manifest_hash)
        manifest = entry.manifest
        inputs = pa.concat_tables(
            [pq.read_table(p) for p in part_files(resolve_under_data_dir(spec.input_dir))],
            promote_options="default",
        )
        output = _conform(pq.read_table(raw / "output.parquet"), inputs.schema)
        decisions = pq.read_table(raw / "decisions.parquet")
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
        output, events = build_events(
            ctx,
            manifest.kind,
            str(entry.extra["op_name"]),
            entry.extra.get("stats_key"),
            dict(entry.extra.get("threshold_params") or {}),
            dict(spec.params),
            inputs,
            output,
            decisions,
        )
        content = ctx.content_columns
        accounting = conservation.check(
            conservation.input_rows(inputs, content, spec.rowkey_scheme),
            output,
            events,
            content,
            spec.rowkey_scheme,
            entry.ref,
        )
    except OperatorError as error:
        write_failure(spec.output_dir, error)
        return {"status": "failed", "code": error.code}
    staged = staging_dir() / f"step-dj-{uuid.uuid4().hex}"
    staged.mkdir(parents=True)
    pq.write_table(output, staged / "part-00000.parquet")
    pq.write_table(
        pa.Table.from_pylist([e.file_row() for e in events], schema=EVENT_SCHEMA),
        staged / EVENTS_FILE,
    )
    meta = {
        "rows_in": accounting.rows_in,
        "rows_kept": accounting.rows_kept,
        "rows_changed": accounting.rows_changed,
        "rows_dropped": accounting.rows_dropped,
        "rows_added": accounting.rows_added,
        "rows_split_assigned": accounting.rows_split_assigned,
        "output_column_roles": dict(spec.column_roles),
        "split_roles": None,
        "operator": entry.ref,
        "manifest_hash": entry.manifest_hash,
        "error": None,
    }
    (staged / META_FILE).write_bytes(canonical_json(meta))
    _publish(staged, resolve_under_data_dir(spec.output_dir))
    shutil.rmtree(raw, ignore_errors=True)
    return {"status": "completed", "rows_in": accounting.rows_in}
