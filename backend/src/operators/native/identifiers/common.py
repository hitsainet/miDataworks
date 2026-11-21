"""Shared pieces of the identifier and text operators (e2e run gaps G0, G3, G5; 2026-10-21)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pyarrow as pa

from ...errors import StepFailed
from ...manifest import ColumnSpec, Kind, OperatorManifest, ResourceSpec

PROVIDER_VERSION = "midataworks-dlp-1"
SYSTEM_PREFIX = "_dw_"
#: Output-prefix rule: letters, digits and underscores, so a prefixed name stays a plain column.
PREFIX_PATTERN = "^[A-Za-z0-9_]{0,40}$"


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
    params: dict[str, Any],
    required: Sequence[str],
    output_columns: Sequence[ColumnSpec] = (),
    version: str = "1",
) -> OperatorManifest:
    """``version`` defaults to "1", so every manifest built before 016 (and its hash) is unchanged
    (016 FTID I10); ``regex_match@2`` passes "2"."""
    return OperatorManifest(
        name=name,
        version=version,
        provider="native",
        provider_version=PROVIDER_VERSION,
        kind=kind,
        scope="row",
        description=description,
        output_columns=tuple(output_columns),
        params_schema=schema(params, required),
        resources=ResourceSpec(queue="curation", cpu_class="light", memory_class="light"),
        deterministic=True,
    )


def refuse(code: str, message: str, details: dict[str, Any] | None = None) -> StepFailed:
    return StepFailed(code, message, details or {})


def column_values(table: pa.Table, column: str, what: str = "column") -> list[Any]:
    """A column's values; refuses a missing column naming the columns there are."""
    if column not in table.schema.names:
        raise refuse(
            "column_not_found",
            f"The {what} {column!r} is not in the step's input. It has: "
            + ", ".join(c for c in table.schema.names if not c.startswith(SYSTEM_PREFIX))
            + ".",
            {"column": column},
        )
    values: list[Any] = table.column(column).to_pylist()
    return values


def text_values(table: pa.Table, column: str, what: str = "column") -> list[str | None]:
    """A column read as text. A non-string value is refused, never silently ``str()``-ed: a
    number where text was declared is the type drift that mislabelled rows (e2e run F1)."""
    out: list[str | None] = []
    for value in column_values(table, column, what):
        if value is None or isinstance(value, str):
            out.append(value)
            continue
        raise refuse(
            "column_not_text",
            f"The {what} {column!r} holds {type(value).__name__} values, not text. Cast it to a "
            "string column first; a value is never converted silently.",
            {"column": column, "found": type(value).__name__},
        )
    return out


def output_names(params: Mapping[str, Any], default_prefix: str, names: Sequence[str]) -> list[str]:
    prefix = params.get("output_prefix")
    prefix = default_prefix if prefix is None else str(prefix)
    return [prefix + n for n in names]


def append_outputs(batch: pa.Table, columns: Sequence[tuple[str, pa.Array]], ref: str) -> pa.Table:
    """Add the step's output columns; an existing column of the same name is refused."""
    clash = [name for name, _ in columns if name in batch.schema.names]
    if clash:
        raise refuse(
            "output_column_exists",
            f"{ref} would write {clash}, which the input already has. Set output_prefix (or "
            "output_column) to a name that is free, or drop the old column first (drop_columns).",
            {"columns": clash},
        )
    out = batch
    for name, array in columns:
        out = out.append_column(name, array)
    return out


def pairs(table: pa.Table) -> list[tuple[str, int]]:
    keys = table.column("_dw_row_key").to_pylist()
    occurrences = table.column("_dw_occurrence").to_pylist()
    return [(str(k), int(o)) for k, o in zip(keys, occurrences, strict=True)]
