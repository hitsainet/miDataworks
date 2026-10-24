"""Shared pieces for feature 004's operators: manifests, reading a whole input, refusals.

Every operator here builds its manifest through :func:`manifest`, reads a dataset-scope input
through :func:`read_all` (the executor's ``ctx.input_reader``; never a path), and refuses a
semantic problem with :func:`refuse`, a ``StepFailed`` carrying 004's stable code so feature 002's
callback, the preview panel and the API all show the same reason (FTID 004 §12).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pyarrow as pa

from ...context import RunContext
from ...errors import StepFailed
from ...manifest import ColumnSpec, Kind, OperatorManifest, ResourceSpec, Scope, ThresholdSpec

PROVIDER_VERSION = "midataworks-004"
SYSTEM_PREFIX = "_dw_"


def schema(properties: dict[str, Any], required: Sequence[str] = ()) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def manifest(
    name: str,
    kind: Kind,
    description: str,
    *,
    scope: Scope = "row",
    params: dict[str, Any] | None = None,
    required: Sequence[str] = (),
    thresholds: Sequence[ThresholdSpec] = (),
    input_columns: Sequence[ColumnSpec] = (),
    output_columns: Sequence[ColumnSpec] = (),
    version: str = "1.0.0",
    deterministic: bool = True,
) -> OperatorManifest:
    return OperatorManifest(
        name=name,
        version=version,
        provider="native",
        provider_version=PROVIDER_VERSION,
        kind=kind,
        scope=scope,
        description=description,
        input_columns=tuple(input_columns),
        output_columns=tuple(output_columns),
        params_schema=schema(params or {}, required),
        thresholds=tuple(thresholds),
        resources=ResourceSpec(queue="curation", cpu_class="medium", memory_class="medium"),
        deterministic=deterministic,
    )


def read_all(ctx: RunContext, columns: list[str] | None = None) -> pa.Table:
    """The whole dataset-scope input (or the named columns) as one table."""
    if ctx.input_reader is None:
        raise StepFailed("input_not_found", "A dataset-scope operator was given no input reader.")
    batches = list(ctx.input_reader(columns))
    if not batches:
        raise StepFailed("input_not_found", "The step's input has no rows or no schema.")
    table = pa.Table.from_batches(batches)
    ctx.check_cancel()
    return table


def read_all_or_empty(ctx: RunContext, batch: pa.Table) -> pa.Table:
    """Like :func:`read_all`, but an empty input yields ``batch`` (the empty input schema)."""
    if ctx.input_reader is None:
        raise StepFailed("input_not_found", "A dataset-scope operator was given no input reader.")
    batches = list(ctx.input_reader(None))
    ctx.check_cancel()
    return pa.Table.from_batches(batches) if batches else batch


def refuse(code: str, message: str, details: dict[str, Any] | None = None) -> StepFailed:
    return StepFailed(code, message, details or {})


def param(params: Mapping[str, Any], name: str, default: Any) -> Any:
    value = params.get(name, default)
    return default if value is None else value


def text_column(table: pa.Table, column: str) -> list[str | None]:
    if column not in table.schema.names:
        raise refuse(
            "invalid_balance_column",
            f"The input has no column {column!r}. Choose one of: "
            + ", ".join(c for c in table.schema.names if not c.startswith(SYSTEM_PREFIX)),
            {"column": column},
        )
    return [None if v is None else str(v) for v in table.column(column).to_pylist()]


def pairs(table: pa.Table) -> list[tuple[str, int]]:
    keys = table.column("_dw_row_key").to_pylist()
    occ = table.column("_dw_occurrence").to_pylist()
    return [(str(k), int(o)) for k, o in zip(keys, occ, strict=True)]


def key_order(table: pa.Table) -> pa.Table:
    """Rows in (row key, occurrence) order, so seeded draws do not depend on file order."""
    import pyarrow.compute as pc

    order = pc.sort_indices(
        table, sort_keys=[("_dw_row_key", "ascending"), ("_dw_occurrence", "ascending")]
    )
    return table.take(order)
