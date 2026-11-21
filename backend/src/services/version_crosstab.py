"""A cross-tab of a version's columns, per split (ENH-006 D116 G5).

The runbook's "read the result before moving on" step cross-tabs a label against plan columns per
split. ``dataworks_get_version_rows`` filters and does not count, so it was a download and a hand
script. This counts the version's files through DuckDB: what is in the rows as built, before any
review decision. A column one split lacks counts as null there, so a split's rows always add up.
"""

from __future__ import annotations

from typing import Any

from ..core.errors import AppError
from ..core.storage import resolve_under_data_dir
from ..models.enums import VersionState
from ..models.version import Version
from .duck import arrow_table, connect, files_param, top_level_columns

MAX_COLUMNS = 4
#: The most combinations one answer lists, across its splits; the largest counts come first.
MAX_CELLS = 2000
#: A long value is cut in the answer: this counts rows, it does not read them.
VALUE_CHARS = 200


def _shown(value: Any) -> Any:
    if isinstance(value, str) and len(value) > VALUE_CHARS:
        return value[:VALUE_CHARS] + "..."
    if isinstance(value, bytes):
        return value.hex()[:VALUE_CHARS]
    if isinstance(value, int | float | bool) or value is None or isinstance(value, str):
        return value
    return str(value)[:VALUE_CHARS]


def scoped_splits(version: Version, split: str | None) -> list[dict[str, Any]]:
    """The version's splits, or the one named. Refuses a version whose rows are not there."""
    if version.state != VersionState.COMPLETED:
        raise AppError(
            f"Version {version.number} is {version.state}, so it has no rows to read.",
            code="version_not_completed",
            status_code=409,
            details={"version_id": str(version.id), "state": str(version.state)},
        )
    if split is None:
        return list(version.splits)
    chosen = [s for s in version.splits if s["name"] == split]
    if not chosen:
        raise AppError(
            f"Version {version.number} has no split {split!r}; it has "
            f"{[s['name'] for s in version.splits]}.",
            code="split_not_found",
            status_code=404,
            details={"split": split, "splits": [s["name"] for s in version.splits]},
        )
    return chosen


def crosstab(version: Version, columns: list[str], split: str | None) -> dict[str, Any]:
    """Rows per split per combination of ``columns``; largest first, at most :data:`MAX_CELLS`."""
    if not 1 <= len(columns) <= MAX_COLUMNS or len(set(columns)) != len(columns):
        raise AppError(
            f"A cross-tab reads 1 to {MAX_COLUMNS} different columns; {columns} was asked for.",
            code="columns_invalid",
            status_code=422,
            details={"columns": columns},
        )
    splits = scoped_splits(version, split)
    per_split: list[dict[str, Any]] = []
    cells: list[dict[str, Any]] = []
    known: set[str] = set()
    con = connect()
    try:
        for item in splits:
            path = resolve_under_data_dir(item["path"])
            present = top_level_columns(con, [path])
            known.update(present)
            selected = ", ".join(
                (
                    f'"{c.replace(chr(34), chr(34) * 2)}" AS "c{i}"'
                    if c in present
                    else f'NULL AS "c{i}"'
                )
                for i, c in enumerate(columns)
            )
            names = ", ".join(f'"c{i}"' for i in range(len(columns)))
            table = arrow_table(
                con,
                f"SELECT {names}, count(*) AS n FROM "  # noqa: S608 - names from the file's schema
                f"(SELECT {selected} FROM read_parquet(?)) GROUP BY {names}",
                [files_param([path])],
            )
            rows = table.to_pylist()
            per_split.append(
                {
                    "name": item["name"],
                    "rows": sum(int(r["n"]) for r in rows),
                    "rows_recorded": int(item["rows"]),
                    "combinations": len(rows),
                    "columns_absent": [c for c in columns if c not in present],
                }
            )
            cells.extend(
                {
                    "split": item["name"],
                    "values": {c: _shown(r[f"c{i}"]) for i, c in enumerate(columns)},
                    "rows": int(r["n"]),
                }
                for r in rows
            )
    finally:
        con.close()
    unknown = sorted(c for c in columns if c not in known)
    if unknown:
        raise AppError(
            f"This version has no column {unknown}.",
            code="unknown_column",
            status_code=422,
            details={"columns": unknown},
        )
    cells.sort(key=lambda c: (-c["rows"], c["split"], str(sorted(c["values"].items()))))
    return {
        "version_id": str(version.id),
        "columns": columns,
        "split": split,
        "splits": per_split,
        "rows_total": sum(s["rows"] for s in per_split),
        "cells_total": len(cells),
        "cells": cells[:MAX_CELLS],
        "cap": MAX_CELLS,
        "truncated": len(cells) > MAX_CELLS,
        "order": "most rows first",
        "reads": "the version's files as built; a review decision is not applied here",
    }
