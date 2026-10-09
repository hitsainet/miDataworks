"""Previews and threshold statistics (FR-003.19, FR-003.20; FTDD 003 sections 5.1 and 7.1; FTID 3.7).

A preview runs an allowed operator on a seeded sample and WRITES NOTHING: no version, no event, no
job row. Results live in Redis for ``OPERATOR_PREVIEW_RESULT_TTL_S`` (one hour) under a key derived
from the request hash, so identical requests within the hour share one run.

Sampling is DuckDB ``USING SAMPLE reservoir(n ROWS) REPEATABLE (seed)`` on ONE thread (DuckDB's
repeatable sampling is reproducible single-threaded only), then ordered by row identity so the same
request shows the same rows in the same order.

A preview has a soft deadline (``preview_timeout``, checked at the operator's cancel checks) and a
hard Celery limit; it is not a long job (R-03.63 covers those).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa

from ..core.canonical_json import canonical_json, canonical_sha256
from ..core.config import get_settings
from ..core.storage import resolve_under_data_dir
from ..services.duck import files_param
from . import conservation, effects
from .context import OccurrenceAllocator, RunContext
from .errors import OperatorError
from .protocol import OperatorResult

logger = logging.getLogger(__name__)

EXCERPT_CHARS = 300
EXAMPLES_PER_OUTCOME = 20
KEY_PREFIX = "dw:operator-preview:"
NOTE_WITHIN_SAMPLE = "within_sample_only"


# --------------------------------------------------------------------------------------------
# Request identity and storage
# --------------------------------------------------------------------------------------------


def request_hash(request: dict[str, Any]) -> str:
    """SHA-256 of the request's canonical JSON (operator, params, input, sample size, seed, mode)."""
    keys = ("operator", "version", "params", "input", "sample_size", "seed", "mode")
    return canonical_sha256({k: request.get(k) for k in keys})


def _redis() -> Any:
    import redis

    return redis.Redis.from_url(get_settings().redis_url, socket_timeout=5)


def store(preview_id: str, body: dict[str, Any]) -> None:
    client = _redis()
    client.set(
        KEY_PREFIX + preview_id,
        canonical_json(body),
        ex=get_settings().operator_preview_result_ttl_s,
    )
    client.delete(KEY_PREFIX + preview_id + ":pending")


def fetch(preview_id: str) -> dict[str, Any] | None:
    """The stored answer, ``{"status": "running"}`` while pending, or None when unknown/expired."""
    import json

    client = _redis()
    raw = client.get(KEY_PREFIX + preview_id)
    if raw is not None:
        loaded: dict[str, Any] = json.loads(raw)
        return loaded
    if client.exists(KEY_PREFIX + preview_id + ":pending"):
        return {"status": "running", "preview_id": preview_id}
    return None


def claim(preview_id: str) -> bool:
    """Mark a preview pending; False when an identical one is already running or stored."""
    client = _redis()
    if client.exists(KEY_PREFIX + preview_id):
        return False
    ttl = int(get_settings().operator_preview_hard_limit_s) + 30
    return bool(client.set(KEY_PREFIX + preview_id + ":pending", b"1", nx=True, ex=ttl))


# --------------------------------------------------------------------------------------------
# Sampling
# --------------------------------------------------------------------------------------------


def _single_thread_connection() -> Any:
    """Like ``services.duck.connect`` (confined to the data volume) but on ONE thread, because
    DuckDB's ``REPEATABLE`` sampling is reproducible only single-threaded."""
    import duckdb

    from ..core.storage import data_dir

    con = duckdb.connect(":memory:")
    con.execute("SET allowed_directories=[?]", [str(data_dir().resolve())])
    con.execute("SET enable_external_access=false")
    con.execute("SET threads=1")
    con.execute("SET lock_configuration=true")
    return con


def select_sample(files: list[Path], n: int, seed: int) -> pa.Table:
    """A reproducible sample of up to ``n`` rows (FTASKS 9.1)."""
    if not files:
        raise OperatorError("input_not_found", "The preview input has no Parquet files.")
    con = _single_thread_connection()
    try:
        # Sample size and seed cannot be bound as parameters; both are validated integers.
        rows, repeat = int(n), int(seed)
        table = con.execute(
            "SELECT * FROM (SELECT * FROM read_parquet(?) "  # noqa: S608 - integers only
            f"USING SAMPLE reservoir({rows} ROWS) REPEATABLE ({repeat})) "
            "ORDER BY _dw_row_key, _dw_occurrence",
            [files_param(files)],
        ).to_arrow_table()
    finally:
        con.close()
    return table


# --------------------------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PreviewInput:
    files: list[Path]
    column_roles: dict[str, str]
    rowkey_scheme: str


def _input(request: dict[str, Any]) -> PreviewInput:
    resolved = request["resolved_input"]
    return PreviewInput(
        [resolve_under_data_dir(p) for p in resolved["files"]],
        dict(resolved["column_roles"]),
        str(resolved["rowkey_scheme"]),
    )


def sample_size_for(manifest: Any, requested: int | None) -> int:
    """Default and cap by whether the operator calls a model (FTDD 003 section 5.5)."""
    settings = get_settings()
    if manifest.resources.endpoint_role is not None:
        default, cap = (
            settings.operator_model_preview_sample_default,
            settings.operator_model_preview_sample_max,
        )
    else:
        default, cap = (
            settings.operator_preview_sample_default,
            settings.operator_preview_sample_max,
        )
    size = default if requested is None else int(requested)
    if size < 1 or size > cap:
        raise OperatorError(
            "params_invalid",
            f"A preview of this operator samples 1 to {cap} rows; {size} was asked for.",
            {"errors": [{"pointer": "/sample_size", "message": f"must be 1..{cap}"}]},
        )
    return size


def _excerpt(row: dict[str, Any], columns: list[str]) -> str:
    for column in columns:
        value = row.get(column)
        if isinstance(value, str):
            return value[:EXCERPT_CHARS]
    return ""


def _deadline_check() -> Any:
    limit = get_settings().operator_preview_soft_limit_s
    deadline = time.monotonic() + limit

    def check() -> None:
        if time.monotonic() > deadline:
            raise OperatorError(
                "preview_timeout",
                f"The preview took longer than {int(limit)} seconds. Run it on a smaller sample.",
            )

    return check


def _context(entry: Any, sample: pa.Table, spec: PreviewInput, seed: int) -> RunContext:
    return RunContext(
        manifest=entry.manifest,
        manifest_hash=str(entry.manifest_hash),
        step_seed=seed,
        job_id=None,
        column_roles=spec.column_roles,
        rowkey_scheme=spec.rowkey_scheme,
        sample=True,
        check_cancel=_deadline_check(),
        allocator=OccurrenceAllocator(conservation.pairs_of(sample)),
    )


def run_preview(registry: Any, request: dict[str, Any]) -> dict[str, Any]:
    """Counts, examples and reasons for a sample. Writes nothing (FTASKS 9.2)."""
    entry = registry.require_allowed(request["operator"], request["version"])
    registry.require_valid_params(request["operator"], request["version"], request["params"])
    spec = _input(request)
    size = sample_size_for(entry.manifest, request.get("sample_size"))
    seed = int(request.get("seed") or 0)
    sample = select_sample(spec.files, size, seed)
    base = {
        "operator": entry.ref,
        "manifest_hash": entry.manifest_hash,
        "sample_size": sample.num_rows,
        "sample_requested": size,
        "seed": seed,
    }
    if sample.num_rows == 0:
        return {**base, "empty": True, "message": "No rows to preview.", "counts": _zero()}
    ctx = _context(entry, sample, spec, seed)
    impl = registry.implementation(entry)
    if entry.manifest.scope == "dataset":

        def reader(columns: list[str] | None) -> Iterator[pa.RecordBatch]:
            table = sample.select(columns) if columns else sample
            yield from table.to_batches()

        ctx.input_reader = reader
        result = impl.run(sample.schema.empty_table(), request["params"], ctx)
    else:
        result = impl.run(sample, request["params"], ctx)
    return {**base, "empty": False, **finish(entry, sample, result, ctx)}


def finish(entry: Any, sample: pa.Table, result: OperatorResult, ctx: RunContext) -> dict[str, Any]:
    """The same checks a step makes, then the shaped answer (counts, examples, reasons)."""
    content = ctx.content_columns
    effects.check(entry.manifest.kind, sample.schema, result, entry.ref)
    inputs = conservation.input_rows(sample, content, ctx.rowkey_scheme)
    accounting = conservation.check(
        inputs, _with_added(result), result.events, content, ctx.rowkey_scheme, entry.ref
    )
    shaped = shape(sample, result, content or list(ctx.column_roles))
    shaped["counts"] = {
        "in": accounting.rows_in,
        "kept": accounting.rows_kept,
        "changed": accounting.rows_changed,
        "dropped": accounting.rows_dropped,
        "added": accounting.rows_added,
        "split_assigned": accounting.rows_split_assigned,
    }
    if entry.manifest.scope == "dataset":
        shaped["note"] = NOTE_WITHIN_SAMPLE
    if result.report is not None:
        # A report or a balancer's preview is its report (feature 004: cells, cap, re-audit,
        # FR-004.40); it describes the SAMPLE, so it is marked as one (FR-004.5).
        shaped["report"] = {**result.report, "sample": True}
    return shaped


def _zero() -> dict[str, int]:
    return dict.fromkeys(("in", "kept", "changed", "dropped", "added", "split_assigned"), 0)


def _with_added(result: OperatorResult) -> pa.Table:
    if result.added is not None and result.added.num_rows:
        return pa.concat_tables([result.output, result.added], promote_options="default")
    return result.output


def shape(sample: pa.Table, result: OperatorResult, text_columns: list[str]) -> dict[str, Any]:
    """Examples of each outcome, with each drop's reason and statistic in full."""
    by_pair = {(r["_dw_row_key"], r["_dw_occurrence"]): r for r in sample.to_pylist()}
    out_rows = {(r["_dw_row_key"], r["_dw_occurrence"]): r for r in _with_added(result).to_pylist()}
    dropped, changed, added = [], [], []
    touched = set()
    for event in result.events:
        pair = (event.row_key, event.occurrence)
        touched.add(pair)
        item = {
            "row_key": event.row_key,
            "occurrence": event.occurrence,
            "reason_code": event.reason_code,
            "reason": event.reason,
            "statistic_name": event.statistic_name,
            "statistic_value": event.statistic_value,
            "statistic_text": event.statistic_text,
            "threshold": event.threshold,
        }
        if event.kind == "dropped":
            item["excerpt"] = _excerpt(by_pair.get(pair, {}), text_columns)
            item["kept_instead"] = event.related_row_key
            dropped.append(item)
        elif event.kind == "changed":
            new = out_rows.get((str(event.new_row_key), int(event.new_occurrence or 0)), {})
            item["before"] = _excerpt(by_pair.get(pair, {}), text_columns)
            item["after"] = _excerpt(new, text_columns)
            changed.append(item)
        elif event.kind == "added":
            item["excerpt"] = _excerpt(out_rows.get(pair, {}), text_columns)
            item["parent_keys"] = list(event.parent_keys or ())
            added.append(item)
    kept = [
        {"row_key": p[0], "occurrence": p[1], "excerpt": _excerpt(r, text_columns)}
        for p, r in by_pair.items()
        if p not in touched and p in out_rows
    ]
    return {
        "examples": {
            "kept": kept[:EXAMPLES_PER_OUTCOME],
            "changed": changed[:EXAMPLES_PER_OUTCOME],
            "dropped": dropped[:EXAMPLES_PER_OUTCOME],
            "added": added[:EXAMPLES_PER_OUTCOME],
        },
        "drops_total": len(dropped),
    }


def run_statistics(registry: Any, request: dict[str, Any]) -> dict[str, Any]:
    """The threshold statistic for every sample row (FR-003.20; FTASKS 9.3)."""
    entry = registry.require_allowed(request["operator"], request["version"])
    manifest = entry.manifest
    if not manifest.thresholds:
        raise OperatorError(
            "no_threshold",
            f"{entry.ref} declares no threshold, so it has no statistic to plot.",
            {"operator": entry.ref},
        )
    registry.require_valid_params(request["operator"], request["version"], request["params"])
    spec = _input(request)
    size = sample_size_for(manifest, request.get("sample_size"))
    seed = int(request.get("seed") or 0)
    sample = select_sample(spec.files, size, seed)
    ctx = _context(entry, sample, spec, seed)
    impl = registry.implementation(entry)
    compute = getattr(impl, "compute_statistics", None)
    if compute is None:  # the registry refuses such an operator; this is the worker's re-check
        raise OperatorError("statistics_missing", f"{entry.ref} cannot compute its statistic.")
    values = compute(sample, request["params"], ctx) if sample.num_rows else {}
    content = ctx.content_columns or list(spec.column_roles)
    rows = sample.to_pylist()
    thresholds = []
    for threshold in manifest.thresholds:
        column = values.get(threshold.statistic)
        if column is None and sample.num_rows:
            raise OperatorError(
                "statistics_missing",
                f"{entry.ref} did not return its {threshold.statistic!r} statistic.",
            )
        series = (
            [] if column is None else [None if v is None else float(v) for v in column.to_pylist()]
        )
        distinct = {v for v in series if v is not None}
        thresholds.append(
            {
                "param": threshold.param,
                "pair_param": threshold.pair_param,
                "statistic": threshold.statistic,
                "unit": threshold.unit,
                "drop_when": threshold.drop_when,
                "current": request["params"].get(threshold.param),
                "constant": len(distinct) <= 1,
                "values": [
                    {
                        "row_key": r["_dw_row_key"],
                        "occurrence": r["_dw_occurrence"],
                        "value": v,
                        "excerpt": _excerpt(r, content),
                    }
                    for r, v in zip(rows, series, strict=True)
                ],
            }
        )
    return {
        "operator": entry.ref,
        "manifest_hash": entry.manifest_hash,
        "sample_size": sample.num_rows,
        "sample_requested": size,
        "seed": seed,
        "empty": sample.num_rows == 0,
        "thresholds": thresholds,
    }


def run_datajuicer(registry: Any, request: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    """Shape the Data-Juicer runner's preview answer exactly as a native preview (FTASKS 9.3)."""
    import json

    from .datajuicer.finalize import DECISION_COLUMNS, build_events
    from .executor import _conform

    entry = registry.require_allowed(request["operator"], request["version"])
    spec = _input(request)
    seed = int(request.get("seed") or 0)
    base = {
        "operator": entry.ref,
        "manifest_hash": entry.manifest_hash,
        "sample_size": len(raw["rows"]),
        "sample_requested": request.get("sample_size_effective"),
        "seed": seed,
    }
    if not raw["rows"]:
        if request.get("mode") == "statistics":
            return {**base, "empty": True, "thresholds": []}
        return {**base, "empty": True, "message": "No rows to preview.", "counts": _zero()}
    sample = pa.Table.from_pylist(raw["rows"])
    output = (
        _conform(pa.Table.from_pylist(raw["output"]), sample.schema)
        if raw["output"]
        else sample.slice(0, 0)
    )
    decisions = pa.Table.from_pylist(
        [{c: d.get(c) for c in DECISION_COLUMNS} for d in raw["decisions"]]
    )
    ctx = _context(entry, sample, spec, seed)
    if request.get("mode") == "statistics":
        stats_key = entry.extra.get("stats_key")
        series = []
        for d in raw["decisions"]:
            value = json.loads(d["stats_json"] or "{}").get(stats_key)
            series.append(float(value) if isinstance(value, int | float) else None)
        rows = sample.to_pylist()
        content = ctx.content_columns or list(spec.column_roles)
        distinct = {v for v in series if v is not None}
        return {
            **base,
            "empty": False,
            "thresholds": [
                {
                    "param": t.param,
                    "pair_param": t.pair_param,
                    "statistic": t.statistic,
                    "unit": t.unit,
                    "drop_when": t.drop_when,
                    "current": request["params"].get(t.param),
                    "constant": len(distinct) <= 1,
                    "values": [
                        {
                            "row_key": r["_dw_row_key"],
                            "occurrence": r["_dw_occurrence"],
                            "value": v,
                            "excerpt": _excerpt(r, content),
                        }
                        for r, v in zip(rows, series, strict=True)
                    ],
                }
                for t in entry.manifest.thresholds
            ],
        }
    output, events = build_events(
        ctx,
        entry.manifest.kind,
        str(entry.extra["op_name"]),
        entry.extra.get("stats_key"),
        dict(entry.extra.get("threshold_params") or {}),
        dict(request["params"]),
        sample,
        output,
        decisions,
    )
    result = OperatorResult(output=output, events=events)
    return {**base, "empty": False, **finish(entry, sample, result, ctx)}


def run_designer(registry: Any, request: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    """Shape the designer worker's preview answer exactly as a native preview."""
    from .data_designer.finalize import build_result

    entry = registry.require_allowed(request["operator"], request["version"])
    spec = _input(request)
    seed = int(request.get("seed") or 0)
    base = {
        "operator": entry.ref,
        "manifest_hash": entry.manifest_hash,
        "sample_size": len(raw["rows"]),
        "sample_requested": request.get("sample_size_effective"),
        "seed": seed,
    }
    if not raw["rows"]:
        return {**base, "empty": True, "message": "No rows to preview.", "counts": _zero()}
    sample = pa.Table.from_pylist(raw["rows"])
    ctx = _context(entry, sample, spec, seed)
    default = entry.manifest.params_schema["properties"]["output_column"].get("default")
    output_column = str(request["params"].get("output_column") or default)
    result = build_result(sample, raw["produced"], output_column, raw["records"], ctx)
    shaped = finish(entry, sample, result, ctx)
    shaped["relay_records"] = raw["records"]
    return {**base, "empty": False, **shaped}


def run_request(request: dict[str, Any], raw: dict[str, Any] | None = None) -> dict[str, Any]:
    """The preview task body: run, then store the answer under the request's preview id."""
    from .registry import current

    preview_id = request["preview_id"]
    try:
        if request.get("designer"):
            if raw is None:
                raise OperatorError(
                    "worker_unavailable", "The Data Designer preview returned nothing."
                )
            result = run_designer(current(), request, raw)
        elif request.get("datajuicer"):
            if raw is None:
                raise OperatorError(
                    "worker_unavailable", "The Data-Juicer preview returned nothing."
                )
            result = run_datajuicer(current(), request, raw)
        elif request.get("mode") == "statistics":
            result = run_statistics(current(), request)
        else:
            result = run_preview(current(), request)
        body = {"status": "done", "preview_id": preview_id, "result": result}
    except OperatorError as error:
        body = {
            "status": "failed",
            "preview_id": preview_id,
            "error": {"code": error.code, "message": error.message, "details": error.details},
        }
    except Exception as exc:  # noqa: BLE001 - the answer says it failed; never a silent pending
        logger.exception("preview %s failed", preview_id)
        body = {
            "status": "failed",
            "preview_id": preview_id,
            "error": {"code": "preview_failed", "message": f"{type(exc).__name__}: {exc}"[:500]},
        }
    store(preview_id, body)
    return {"preview_id": preview_id, "status": body["status"]}
