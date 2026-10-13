"""Read-only DuckDB over Parquet under the data volume (FR-002.33; FTID 002 section 11).

What it guarantees:
- A connection reads files under ``DATA_DIR`` only: ``allowed_directories`` names the volume,
  ``enable_external_access`` is off and the configuration is locked, so a query cannot reach
  another path or the network, and cannot turn the restriction back off.
- Values are always bound parameters. Identifiers (column names) are checked against the file's
  own schema and quoted; anything else is refused (``unknown_column``).
- Structured filters (``[{column, op, value}]``) are the only filter language; free SQL is never
  accepted (FTDD 002 section 5.8).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import duckdb

from ..core.errors import AppError
from ..core.storage import data_dir

#: Filter operators and their SQL. ``contains`` is a case-insensitive substring on text.
FILTER_OPS: dict[str, str] = {
    "eq": "{c} = ?",
    "ne": "{c} <> ?",
    "lt": "{c} < ?",
    "le": "{c} <= ?",
    "gt": "{c} > ?",
    "ge": "{c} >= ?",
    "contains": "contains(lower(CAST({c} AS VARCHAR)), lower(?))",
    "is_null": "{c} IS NULL",
    "not_null": "{c} IS NOT NULL",
}


def connect() -> duckdb.DuckDBPyConnection:
    """A fresh in-memory connection confined to the data volume."""
    con = duckdb.connect(":memory:")
    con.execute("SET allowed_directories=[?]", [str(data_dir().resolve())])
    con.execute("SET enable_external_access=false")
    con.execute("SET threads=2")
    con.execute("SET lock_configuration=true")
    return con


def quote_ident(name: str, allowed: Iterable[str]) -> str:
    """Quote ``name`` after checking it is one of the file's columns."""
    if name not in set(allowed):
        raise AppError(
            f"There is no column {name!r} here.",
            code="unknown_column",
            status_code=422,
            details={"column": name},
        )
    return '"' + name.replace('"', '""') + '"'


def top_level_columns(con: duckdb.DuckDBPyConnection, files: Sequence[Path]) -> list[str]:
    """Top-level column names of the files, in file order."""
    description = con.execute(
        "SELECT * FROM read_parquet(?) LIMIT 0", [[str(f) for f in files]]
    ).description
    return [d[0] for d in description or []]


def where_clause(
    filters: Sequence[dict[str, Any]], columns: Sequence[str]
) -> tuple[str, list[Any]]:
    """``WHERE`` text and parameters for structured filters; refuses unknown columns and ops."""
    parts: list[str] = []
    params: list[Any] = []
    for item in filters:
        op = item.get("op")
        if op not in FILTER_OPS:
            raise AppError(
                f"Unknown filter operator {op!r}; use one of {sorted(FILTER_OPS)}.",
                code="filter_invalid",
                status_code=422,
            )
        column = quote_ident(str(item.get("column")), columns)
        parts.append(FILTER_OPS[op].format(c=column))
        if op not in {"is_null", "not_null"}:
            params.append(item.get("value"))
    return (" WHERE " + " AND ".join(parts)) if parts else "", params


def files_param(files: Sequence[Path]) -> list[str]:
    return [str(f) for f in files]
