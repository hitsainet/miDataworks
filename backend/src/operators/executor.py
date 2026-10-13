"""The step executor (FR-003.17, FR-003.18, FR-003.25; FTDD 003 sections 2.2 and 6; FTID 003 section 3.6).

``dispatch_step`` (API/orchestrator side) re-validates the parameters and the allowlist, compares
the manifest hash, picks the task by the manifest's queue and sends it with a Celery ``link`` to
feature 002's callback. No task waits synchronously on another (a one-process worker deadlocks).

``execute_in_process`` (worker side) runs one operator over one input:

1. the worker RE-CHECKS everything the API checked: allowlist (FR-003.12), manifest hash
   (``manifest_mismatch``, image skew during a rollout) and parameters (FR-003.7);
2. row scope: streams record batches from DuckDB, calls ``run`` per batch, and checks the kind's
   effects and conservation PER BATCH; dataset scope: one ``run`` with ``ctx.input_reader`` and the
   same checks against the whole input identity set;
3. polls cancellation at batch boundaries and heartbeats on TIME (the step row's ``heartbeat_at``,
   which the build's janitor liveness reads, and ``record_progress`` on the job);
4. writes ``part-*.parquet``, ``events.parquet`` and ``meta.json`` under ``staging/`` and renames
   the directory into the 002-provided ``output_dir`` only when every batch passed. A violation
   publishes NOTHING but a ``meta.json`` carrying the error, which 002's callback turns into a
   failed step with the code (FR-003.9, 6.7).

A ``GenerationStageSpec`` (FR-003.25, for feature 007) runs the identical checks with no step
execution, no 002 link and output to the caller's staging path.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from ..core import clock
from ..core.cancellation import CancelCheck, record_progress
from ..core.canonical_json import canonical_json
from ..core.config import get_settings
from ..core.storage import resolve_under_data_dir, staging_dir
from ..services.duck import connect, files_param
from ..services.operator_port import StepSpec
from ..services.step_contract import EVENT_SCHEMA, EVENTS_FILE, META_FILE, part_files
from . import conservation, effects, endpoint_port
from .context import OccurrenceAllocator, RunContext
from .errors import OperatorError, StepFailed
from .protocol import OperatorResult, RowEvent

logger = logging.getLogger(__name__)

#: The step task for each queue a manifest can name (ADR-006; routed by TASK_ROUTES).
TASK_FOR_QUEUE: dict[str, str] = {
    "curation": "midataworks.operators.step.curation",
    "labeling": "midataworks.operators.step.labeling",
    "datajuicer": "midataworks.datajuicer.step",
    "designer": "midataworks.designer.step",
}
FINALIZE_DATAJUICER = "midataworks.operators.step.finalize_datajuicer"
FINALIZE_DESIGNER = "midataworks.operators.step.finalize_designer"
#: Queues whose operators run only in their own image; this process never runs them.
REMOTE_QUEUES = frozenset({"datajuicer", "designer"})


# --------------------------------------------------------------------------------------------
# Specs and results
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class GenerationStageSpec:
    """One operator over one batch for feature 007, outside any recipe step (FR-003.25)."""

    job_id: str
    operator: str
    version: str
    expected_manifest_hash: str
    params: dict[str, Any]
    #: Relative to ``DATA_DIR`` (staging), where the stage's output goes; the caller commits it.
    output_dir: str
    step_seed: int
    column_roles: dict[str, str]
    rowkey_scheme: str
    #: A staged input directory (``part-*.parquet``) relative to ``DATA_DIR``, or a table.
    input_dir: str | None = None
    input_table: pa.Table | None = None
    body_overrides: dict[str, Any] | None = None
    #: Feature 007: the miLLM lease the CALLER holds (005's shared holder), so a model-calling
    #: operator's relay sends ``X-miLLM-Lease``. Never logged or written (repr=False).
    lease_id: str | None = field(default=None, repr=False)


@dataclass
class StepResult:
    output_dir: str
    rows_in: int = 0
    rows_kept: int = 0
    rows_changed: int = 0
    rows_dropped: int = 0
    rows_added: int = 0
    rows_split_assigned: int = 0
    #: P-13: whether the model the step used was held under a lease, and which model.
    pinned: bool | None = None
    model: str | None = None
    relay_records: list[dict[str, Any]] = field(default_factory=list)
    seconds: float = 0.0


# --------------------------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------------------------


def send_task(name: str, **options: Any) -> Any:
    """The one place a step leaves this process (tests replace it with a recording app)."""
    from ..core.celery_app import celery_app

    return celery_app.send_task(name, **options)


def signature(name: str, args: list[Any], kwargs: dict[str, Any] | None = None) -> Any:
    from ..core.celery_app import linked_signature

    return linked_signature(name, args=args, kwargs=kwargs or {}, immutable=True)


def check_manifest_hash(entry: Any, expected: str) -> None:
    if entry.manifest_hash != expected:
        raise StepFailed(
            "manifest_mismatch",
            f"{entry.ref}'s manifest changed since this step was planned (expected "
            f"{expected[:12]}, found {str(entry.manifest_hash)[:12]}). This happens while the "
            "API and workers run different images during a rollout; build again once it ends.",
            {"expected": expected, "found": entry.manifest_hash},
        )


def dispatch_step(registry: Any, spec: StepSpec, link_task: str, link_args: list[Any]) -> None:
    """Validate, then send the step to its queue with 002's callback linked (FTID 3.6)."""
    entry = registry.require_allowed(spec.operator, spec.version)
    registry.require_valid_params(spec.operator, spec.version, spec.params)
    check_manifest_hash(entry, spec.expected_manifest_hash)
    queue = entry.manifest.resources.queue
    payload = spec.as_payload()
    if queue == "datajuicer":
        from .datajuicer.adapter import runner_payload

        finalize = signature(FINALIZE_DATAJUICER, [payload, link_task, list(link_args)])
        on_error = signature(
            FINALIZE_DATAJUICER, [payload, link_task, list(link_args)], {"failed": True}
        )
        send_task(
            TASK_FOR_QUEUE[queue],
            args=[runner_payload(entry, spec)],
            link=finalize,
            link_error=on_error,
        )
        return
    if queue == "designer":
        from .data_designer import handoff
        from .data_designer.adapter import step_payload

        endpoint = None
        key_ref = None
        role = entry.manifest.resources.endpoint_role
        if role is not None:
            endpoint = endpoint_port.resolver().resolve(role)
            if endpoint.api_key:
                key_ref = f"step:{spec.step_execution_id}:{uuid.uuid4().hex}"
                handoff.put(key_ref, endpoint.api_key)
        finalize = signature(FINALIZE_DESIGNER, [payload, link_task, list(link_args)])
        on_error = signature(
            FINALIZE_DESIGNER, [payload, link_task, list(link_args)], {"failed": True}
        )
        send_task(
            TASK_FOR_QUEUE[queue],
            args=[step_payload(entry, spec, endpoint, key_ref)],
            link=finalize,
            link_error=on_error,
        )
        return
    send_task(
        TASK_FOR_QUEUE[queue],
        args=[payload],
        link=signature(link_task, list(link_args)),
    )


# --------------------------------------------------------------------------------------------
# Input reading
# --------------------------------------------------------------------------------------------


class _Input:
    """The step's input: Parquet parts read through DuckDB, or an in-memory table."""

    def __init__(self, files: list[Path], table: pa.Table | None = None) -> None:
        self.files = files
        self.table = table

    def schema(self) -> pa.Schema:
        if self.table is not None:
            return self.table.schema
        if not self.files:
            raise OperatorError("input_not_found", "The step's input has no Parquet parts.")
        return pq.read_schema(self.files[0])

    def batches(self, rows: int, columns: list[str] | None = None) -> Iterator[pa.RecordBatch]:
        if self.table is not None:
            table = self.table.select(columns) if columns else self.table
            yield from table.to_batches(max_chunksize=rows)
            return
        if not self.files:
            return
        con = connect()
        try:
            select = (
                "*" if not columns else ", ".join('"' + c.replace('"', '""') + '"' for c in columns)
            )
            reader = con.execute(
                f"SELECT {select} FROM read_parquet(?)",  # noqa: S608 - names quoted above
                [files_param(self.files)],
            ).to_arrow_reader(rows)
            yield from reader
        finally:
            con.close()


def _input_pairs(source: _Input, rows: int) -> list[tuple[str, int]]:
    pairs: list[tuple[str, int]] = []
    for batch in source.batches(rows, ["_dw_row_key", "_dw_occurrence"]):
        pairs.extend(conservation.pairs_of(batch))
    return pairs


def _conform(table: pa.Table, schema: pa.Schema) -> pa.Table:
    """Give ``table`` exactly ``schema``'s columns and types (nulls for a missing column)."""
    columns = []
    for fld in schema:
        if fld.name in table.schema.names:
            column = table.column(fld.name)
            columns.append(column if column.type == fld.type else column.cast(fld.type))
        else:
            columns.append(pa.nulls(table.num_rows, fld.type))
    return pa.Table.from_arrays(columns, schema=schema)


def _normalise_input(table: pa.Table, schema: pa.Schema) -> pa.Table:
    """DuckDB may widen types on the way out (large strings); restore the file's own schema."""
    if table.schema.equals(schema):
        return table
    return _conform(table, schema)


# --------------------------------------------------------------------------------------------
# Heartbeat
# --------------------------------------------------------------------------------------------


class Heartbeat:
    """Time-throttled sign of life for a long step (ADR-007): the step row and the job row."""

    def __init__(
        self,
        job_id: str | None,
        step_execution_id: str | None,
        label: str,
        renew: Callable[[], None] | None = None,
    ) -> None:
        self.job_id = job_id
        self.step_execution_id = step_execution_id
        self.label = label
        self.renew = renew
        self._last: float | None = None

    def tick(self, done: int, total: int | None, *, force: bool = False) -> bool:
        interval = get_settings().progress_heartbeat_seconds
        now = clock.monotonic()
        if not force and self._last is not None and now - self._last < interval:
            return False
        self._last = now
        if self.step_execution_id is not None:
            self._stamp_step()
        if self.job_id is not None:
            of = f" of {total}" if total is not None else ""
            record_progress(self.job_id, message=f"{self.label}: {done}{of} rows processed")
        if self.renew is not None:
            self.renew()
        return True

    def _stamp_step(self) -> None:
        from sqlalchemy import update

        from ..core.database import get_sync_db
        from ..models.step_execution import StepExecution

        try:
            with get_sync_db() as session:
                session.execute(
                    update(StepExecution)
                    .where(StepExecution.id == self.step_execution_id)
                    .values(heartbeat_at=clock.utc_now())
                )
                session.commit()
        except Exception as exc:  # noqa: BLE001 - narration must not break the work
            logger.warning("Could not stamp step %s: %s", self.step_execution_id, exc)


# --------------------------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------------------------


def _registry() -> Any:
    from .registry import current

    return current()


def _write_meta(directory: Path, meta: dict[str, Any]) -> None:
    (directory / META_FILE).write_bytes(canonical_json(meta))


def _events_table(events: list[RowEvent]) -> pa.Table:
    return pa.Table.from_pylist([e.file_row() for e in events], schema=EVENT_SCHEMA)


def _publish(staged: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        shutil.rmtree(destination)
    os.replace(staged, destination)


def write_failure(output_dir: str, error: OperatorError | dict[str, Any]) -> None:
    """Publish ONLY a ``meta.json`` carrying the error, so 002's callback fails the step with it."""
    body = (
        error
        if isinstance(error, dict)
        else {"code": error.code, "message": error.message, "details": error.details}
    )
    staged = staging_dir() / f"step-failed-{uuid.uuid4().hex}"
    staged.mkdir(parents=True)
    _write_meta(staged, {"error": body})
    _publish(staged, resolve_under_data_dir(output_dir))


def execute_in_process(spec: StepSpec | GenerationStageSpec, *, registry: Any = None) -> StepResult:
    """Run one step or generation stage in this process. Raises ``StepFailed`` on a violation."""
    started = time.monotonic()
    registry = registry or _registry()
    # --- the worker's own checks (FR-003.7, FR-003.12) ---
    entry = registry.require_allowed(spec.operator, spec.version)
    check_manifest_hash(entry, spec.expected_manifest_hash)
    registry.require_valid_params(spec.operator, spec.version, spec.params)
    manifest = entry.manifest
    if manifest.resources.queue in REMOTE_QUEUES:
        worker = "Data-Juicer" if manifest.resources.queue == "datajuicer" else "Data Designer"
        raise StepFailed(
            "worker_unavailable",
            f"{entry.ref} runs only in the {worker} worker; it cannot run in this process. "
            "Dispatch it as a step (feature 007: a generation stage on Data Designer needs the "
            "designer queue, recorded as an open item).",
        )
    impl = registry.implementation(entry)
    is_step = isinstance(spec, StepSpec)
    if isinstance(spec, GenerationStageSpec) and spec.input_table is not None:
        source = _Input([], spec.input_table)
    else:
        input_dir = spec.input_dir
        if input_dir is None:
            raise OperatorError("input_not_found", "The generation stage names no input.")
        source = _Input(part_files(resolve_under_data_dir(input_dir)))
    input_schema = source.schema()
    settings = get_settings()
    batch_rows = settings.operator_batch_rows

    # --- endpoint and lease (FR-003.18, P-05, P-13) ---
    lease = None
    endpoint = None
    if manifest.resources.endpoint_role is not None:
        endpoint = endpoint_port.resolver().resolve(manifest.resources.endpoint_role)
        if manifest.resources.needs_lease:
            lease = endpoint_port.lease_manager().acquire(endpoint)

    job_id = spec.job_id
    step_id = spec.step_execution_id if isinstance(spec, StepSpec) else None
    heartbeat = Heartbeat(
        job_id if is_step else None,
        step_id,
        f"Step {entry.ref}",
        renew=(lambda: endpoint_port.lease_manager().renew(lease)) if lease else None,
    )
    cancel = CancelCheck(job_id) if is_step else None

    def check_cancel() -> None:
        if cancel is not None:
            cancel.raise_if_cancelled("Stopped at a batch boundary; completed batches are staged.")

    # The whole input identity set: occurrences for changed and added rows never collide.
    allocator = OccurrenceAllocator(_input_pairs(source, batch_rows))
    ctx = RunContext(
        manifest=manifest,
        manifest_hash=str(entry.manifest_hash),
        step_seed=int(spec.step_seed),
        job_id=job_id,
        column_roles=dict(spec.column_roles),
        rowkey_scheme=spec.rowkey_scheme,
        step_execution_id=step_id,
        check_cancel=check_cancel,
        allocator=allocator,
        body_overrides=spec.body_overrides if isinstance(spec, GenerationStageSpec) else None,
        lease_id=spec.lease_id if isinstance(spec, GenerationStageSpec) else None,
        bindings=list(spec.bindings) if isinstance(spec, StepSpec) else [],
    )
    if endpoint is not None:
        # Seed the cached property with the endpoint the lease was taken on: one resolution.
        ctx.__dict__["endpoint"] = endpoint
    content = ctx.content_columns
    scheme = spec.rowkey_scheme
    staged = staging_dir() / f"step-{uuid.uuid4().hex}"
    staged.mkdir(parents=True)
    totals = conservation.Accounting()
    roles = dict(spec.column_roles)
    for column in manifest.output_columns:
        roles.setdefault(column.name, column.role or "metadata")
    split_roles: dict[str, Any] | None = None
    out_writer: pq.ParquetWriter | None = None
    out_schema: pa.Schema | None = None
    event_writer = pq.ParquetWriter(staged / EVENTS_FILE, EVENT_SCHEMA)
    done = 0

    def write(result: OperatorResult) -> None:
        nonlocal out_writer, out_schema, split_roles
        table = result.output
        if result.added is not None and result.added.num_rows:
            table = pa.concat_tables([table, result.added], promote_options="default")
        if out_schema is None:
            out_schema = table.schema
            out_writer = pq.ParquetWriter(staged / "part-00000.parquet", out_schema)
        assert out_writer is not None
        out_writer.write_table(_conform(table, out_schema))
        if result.events:
            event_writer.write_table(_events_table(result.events))
        if result.output_roles:
            roles.update(result.output_roles)
        if result.report and "split_roles" in result.report:
            split_roles = dict(result.report["split_roles"])

    def check(result: OperatorResult, inputs: dict[Any, Any], batch_schema: pa.Schema) -> None:
        effects.check(manifest.kind, batch_schema, result, entry.ref)
        output = result.output
        if result.added is not None and result.added.num_rows:
            output = pa.concat_tables([output, result.added], promote_options="default")
        totals.add(conservation.check(inputs, output, result.events, content, scheme, entry.ref))

    try:
        heartbeat.tick(0, None, force=True)
        if manifest.scope == "dataset":
            check_cancel()
            inputs: dict[Any, Any] = {}
            id_columns = ["_dw_row_key", "_dw_occurrence"] + content
            if "_dw_split" in input_schema.names:
                id_columns.append("_dw_split")
            for record_batch in source.batches(batch_rows, id_columns):
                inputs.update(
                    conservation.input_rows(pa.Table.from_batches([record_batch]), content, scheme)
                )

            def reader(columns: list[str] | None) -> Iterator[pa.RecordBatch]:
                for record_batch in source.batches(batch_rows, columns):
                    table = _normalise_input(
                        pa.Table.from_batches([record_batch]),
                        (
                            input_schema
                            if not columns
                            else pa.schema([input_schema.field(c) for c in columns])
                        ),
                    )
                    yield from table.to_batches()

            ctx.input_reader = reader
            result = impl.run(input_schema.empty_table(), spec.params, ctx)
            check(result, inputs, input_schema)
            write(result)
            done = len(inputs)
        else:
            for record_batch in source.batches(batch_rows):
                check_cancel()
                batch = _normalise_input(pa.Table.from_batches([record_batch]), input_schema)
                inputs = conservation.input_rows(batch, content, scheme)
                result = impl.run(batch, spec.params, ctx)
                check(result, inputs, input_schema)
                write(result)
                done += batch.num_rows
                heartbeat.tick(done, None)
        if out_writer is None:  # empty input: an empty part with the input's schema
            out_writer = pq.ParquetWriter(staged / "part-00000.parquet", input_schema)
        out_writer.close()
        out_writer = None
        event_writer.close()
        meta = {
            "rows_in": totals.rows_in,
            "rows_kept": totals.rows_kept,
            "rows_changed": totals.rows_changed,
            "rows_dropped": totals.rows_dropped,
            "rows_added": totals.rows_added,
            "rows_split_assigned": totals.rows_split_assigned,
            "output_column_roles": roles,
            "split_roles": split_roles,
            "pinned": (lease is not None) if endpoint is not None else None,
            "model": (lease.model if lease else (endpoint.model if endpoint else None)),
            "operator": entry.ref,
            "manifest_hash": entry.manifest_hash,
            "relay_records": len(ctx.relay_records),
            "error": None,
        }
        _write_meta(staged, meta)
        _publish(staged, resolve_under_data_dir(spec.output_dir))
    except BaseException:
        if out_writer is not None:
            out_writer.close()
        event_writer.close()
        raise
    finally:
        if lease is not None:
            endpoint_port.lease_manager().release(lease)
    logger.info(
        "step %s %s: in %d kept %d changed %d dropped %d added %d",
        step_id or "(stage)",
        entry.ref,
        totals.rows_in,
        totals.rows_kept,
        totals.rows_changed,
        totals.rows_dropped,
        totals.rows_added,
    )
    return StepResult(
        output_dir=spec.output_dir,
        rows_in=totals.rows_in,
        rows_kept=totals.rows_kept,
        rows_changed=totals.rows_changed,
        rows_dropped=totals.rows_dropped,
        rows_added=totals.rows_added,
        rows_split_assigned=totals.rows_split_assigned,
        pinned=(lease is not None) if endpoint is not None else None,
        model=meta["model"],
        relay_records=list(ctx.relay_records),
        seconds=time.monotonic() - started,
    )


#: FTID 003 section 3.4: feature 002's FTDD names the in-process entry ``execute_step``.
execute_step = execute_in_process
