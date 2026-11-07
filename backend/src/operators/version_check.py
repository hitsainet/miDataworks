"""A check operator over every row of a version (ENH-006 D116 G8).

``dataworks_preview_operator`` stops at a sample, so a pass there proves nothing about the rows it did
not read. This runs one operator whose manifest says ``check`` over all of a version, or one split,
under the same kind and conservation checks a build step makes, and writes nothing: no version, no
event, no file. The answer is the job's result, ``pass`` or the refusal with its code and details.

A check over a whole version reads every split as one input, because what it looks for can cross them
(a carrier id two runs hold does not stop at a split). Never passes by reading less: a pass whose count
of rows read differs from the version's recorded count is refused.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from ..core.errors import AppError
from ..services.duck import arrow_reader, connect, files_param
from . import conservation, effects
from .context import OccurrenceAllocator, RunContext
from .errors import StepFailed
from .executor import _Input, _input_pairs, _jsonable, _normalise_input
from .protocol import OperatorResult

PASSED = "pass"
REFUSED = "refused"
#: Rows per batch: this reads the version's own files, whatever the build's setting was.
BATCH_ROWS = 10_000


class _VersionInput(_Input):
    """A version's split files read as one input. Splits may differ by a column only one of them
    holds values in (an all-null column reads as Arrow ``null``), so they are read by name."""

    def schema(self) -> pa.Schema:
        return pa.unify_schemas(
            [pq.read_schema(f) for f in self.files], promote_options="permissive"
        )

    def batches(self, rows: int, columns: list[str] | None = None) -> Iterator[pa.RecordBatch]:
        con = connect()
        try:
            select = (
                "*" if not columns else ", ".join('"' + c.replace('"', '""') + '"' for c in columns)
            )
            yield from arrow_reader(
                con,
                f"SELECT {select} FROM read_parquet(?, union_by_name=true)",  # noqa: S608 - quoted
                [files_param(self.files)],
                rows,
            )
        finally:
            con.close()


def require_check(entry: Any) -> None:
    """Whether this operator may run here is its manifest's word, never a list kept in this file."""
    if entry.manifest is None or not entry.manifest.check:
        raise StepFailed(
            "operator_not_a_check",
            f"{entry.ref} is not a check: its manifest does not say it changes no row, so it cannot "
            "run over a whole version here. dataworks_list_operators marks the checks with check: "
            "true. To run any other operator, add it to a recipe.",
            {
                "operator": entry.ref,
                "next": {
                    "tool": "dataworks_list_operators",
                    "args": {},
                    "why": "find the operators whose check field is true",
                },
            },
        )


def _refuse_a_changed_row(result: OperatorResult, schema: pa.Schema, ref: str) -> None:
    moved = (
        [e.kind for e in result.events]
        + (["added"] if result.added is not None and result.added.num_rows else [])
        + [c for c in result.output.schema.names if c not in schema.names]
    )
    if moved:
        raise StepFailed(
            "check_changed_rows",
            f"{ref} says it is a check, and it changed the rows it read: {sorted(set(moved))}.",
            {"operator": ref, "changes": sorted(set(moved))},
        )


def _execute(
    entry: Any,
    impl: Any,
    params: dict[str, Any],
    files: list[Path],
    ctx: RunContext,
) -> tuple[int, list[dict[str, Any]]]:
    """Rows read and the operator's reports. Raises the operator's refusal."""
    source = _VersionInput(files)
    schema = source.schema()
    content = ctx.content_columns
    scheme = ctx.rowkey_scheme
    ctx.allocator = OccurrenceAllocator(_input_pairs(source, BATCH_ROWS))
    totals = conservation.Accounting()
    reports: list[dict[str, Any]] = []

    def checked(result: OperatorResult, inputs: dict[Any, Any]) -> None:
        effects.check(entry.manifest.kind, schema, result, entry.ref, [])
        _refuse_a_changed_row(result, schema, entry.ref)
        totals.add(
            conservation.check(inputs, result.output, result.events, content, scheme, entry.ref)
        )
        if result.report:
            reports.append(_jsonable(result.report))

    if entry.manifest.scope == "dataset":
        ctx.check_cancel()
        inputs: dict[Any, Any] = {}
        id_columns = ["_dw_row_key", "_dw_occurrence", *content]
        if "_dw_split" in schema.names:
            id_columns.append("_dw_split")
        for record_batch in source.batches(BATCH_ROWS, id_columns):
            inputs.update(
                conservation.input_rows(pa.Table.from_batches([record_batch]), content, scheme)
            )

        def reader(columns: list[str] | None) -> Iterator[pa.RecordBatch]:
            shape = schema if not columns else pa.schema([schema.field(c) for c in columns])
            for record_batch in source.batches(BATCH_ROWS, columns):
                table = _normalise_input(pa.Table.from_batches([record_batch]), shape)
                yield from table.to_batches()

        ctx.input_reader = reader
        checked(impl.run(schema.empty_table(), params, ctx), inputs)
        return totals.rows_in, reports
    for record_batch in source.batches(BATCH_ROWS):
        ctx.check_cancel()
        batch = _normalise_input(pa.Table.from_batches([record_batch]), schema)
        checked(impl.run(batch, params, ctx), conservation.input_rows(batch, content, scheme))
    return totals.rows_in, reports


def run_check(
    registry: Any,
    *,
    operator: str,
    version: str,
    params: dict[str, Any],
    files: list[Path],
    expected_rows: int,
    column_roles: dict[str, str],
    rowkey_scheme: str,
    seed: int,
    job_id: str | None,
    check_cancel: Callable[[], None],
) -> dict[str, Any]:
    """The check's answer: ``verdict`` is ``pass`` or ``refused``, with the refusal's code, message
    and details. A fault that is not a refusal (a crash) raises, and fails the job."""
    base: dict[str, Any] = {"operator": f"{operator}@{version}"}
    try:
        entry = registry.require_allowed(operator, version)
        require_check(entry)
        registry.require_valid_params(operator, version, params)
        base["manifest_hash"] = entry.manifest_hash
        ctx = RunContext(
            manifest=entry.manifest,
            manifest_hash=str(entry.manifest_hash),
            step_seed=seed,
            job_id=job_id,
            column_roles=column_roles,
            rowkey_scheme=rowkey_scheme,
            check_cancel=check_cancel,
        )
        rows, reports = _execute(entry, registry.implementation(entry), params, files, ctx)
        if rows != expected_rows:
            raise StepFailed(
                "check_read_too_few_rows",
                f"The check read {rows:,} rows of the {expected_rows:,} the version records, so "
                "its pass would not cover them.",
                {"rows_read": rows, "rows_recorded": expected_rows},
            )
    except AppError as refusal:
        return {
            **base,
            "verdict": REFUSED,
            "refusal": {
                "code": refusal.code,
                "message": refusal.message,
                "details": _jsonable(refusal.details),
            },
        }
    return {**base, "verdict": PASSED, "rows_checked": rows, "reports": reports}


__all__ = ["PASSED", "REFUSED", "require_check", "run_check"]
