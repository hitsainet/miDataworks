"""Reading a label run's input rows from the version's Parquet through DuckDB (FTID 005 7.3, 7.4).

- Rows are identified by ``_dw_row_key``; a key's duplicate occurrences carry identical content
  (002's row key hashes the content columns), so a run labels each key ONCE, reading its lowest
  occurrence.
- ``row_filter`` (FR-005.53) restricts to ``_dw_origin = ?`` BEFORE counting, so the plan, the
  P-07 count and the approval decision all use the filtered count.
- Already-recorded keys are removed with a DuckDB anti-join against an Arrow table of recorded
  keys (never a Python set of millions of strings).
- Values are always bound parameters; column names are checked against the file's own schema
  (``duck.quote_ident``). Row text never reaches SQL.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import pyarrow as pa

from ..core.errors import AppError
from ..core.storage import resolve_under_data_dir
from .duck import connect, files_param, quote_ident, top_level_columns

ROW_KEY = "_dw_row_key"
ORIGIN = "_dw_origin"


def version_files(splits: Sequence[Mapping[str, Any]]) -> list[Path]:
    return [resolve_under_data_dir(str(s["path"])) for s in splits]


def _origin_clause(
    row_filter: Mapping[str, str] | None, columns: Sequence[str]
) -> tuple[str, list[Any]]:
    if not row_filter:
        return "", []
    if ORIGIN not in columns:
        raise AppError(
            "This version records no row origin, so it cannot be filtered by origin.",
            code="row_filter_unsupported",
            status_code=422,
        )
    return f" AND {quote_ident(ORIGIN, columns)} = ?", [row_filter["origin"]]


def check_field_map(
    files: Sequence[Path], field_map: Mapping[str, str], input_fields: Sequence[str]
) -> None:
    """Every template input field is mapped, and every mapped column exists in the version."""
    missing = [f for f in input_fields if f not in field_map]
    if missing:
        raise AppError(
            f"Map the template's input field(s) {missing} to columns of the version.",
            code="FIELD_MAP_INCOMPLETE",
            status_code=422,
            details={"missing": missing},
        )
    con = connect()
    try:
        columns = top_level_columns(con, files)
    finally:
        con.close()
    for column in field_map.values():
        quote_ident(column, columns)


def column_type(files: Sequence[Path], column: str) -> pa.DataType:
    """The Arrow type of ``column`` in the version's files (every split shares one schema)."""
    import pyarrow.parquet as pq

    return pq.read_schema(files[0]).field(column).type


def _distinct_sql(
    files: Sequence[Path],
    select_cols: str,
    row_filter: Mapping[str, str] | None,
    columns: list[str],
) -> tuple[str, list[Any]]:
    clause, params = _origin_clause(row_filter, columns)
    key = quote_ident(ROW_KEY, columns)
    sql = (
        f"SELECT {select_cols} FROM read_parquet(?) WHERE {key} IS NOT NULL{clause} "  # noqa: S608 - identifiers quoted
        f'QUALIFY row_number() OVER (PARTITION BY {key} ORDER BY "_dw_occurrence") = 1'
    )
    return sql, [files_param(files), *params]


def count_rows(files: Sequence[Path], row_filter: Mapping[str, str] | None) -> int:
    con = connect()
    try:
        columns = top_level_columns(con, files)
        key = quote_ident(ROW_KEY, columns)
        clause, params = _origin_clause(row_filter, columns)
        value = con.execute(
            f"SELECT count(DISTINCT {key}) FROM read_parquet(?) WHERE {key} IS NOT NULL{clause}",  # noqa: S608 - identifiers quoted
            [files_param(files), *params],
        ).fetchone()
        return int(value[0]) if value else 0
    finally:
        con.close()


def coverage(files: Sequence[Path], row_filter: Mapping[str, str] | None) -> dict[str, int]:
    """The rows a run covers and the distinct keys it scores (2026-10-08 live finding 2).

    002's row key is the row's content (ADR-005, T-07), so identical inputs share one key and one
    label that applies to every copy. A run scores ``row_keys`` inputs and covers ``rows`` rows;
    the reproduction gate and a reproduction link state the same two numbers over their rows
    (``detector_sets.key_labels.summary``), so the three never disagree about what they counted.
    """
    con = connect()
    try:
        columns = top_level_columns(con, files)
        key = quote_ident(ROW_KEY, columns)
        clause, params = _origin_clause(row_filter, columns)
        inner = f"SELECT {key}, count(*) AS n FROM read_parquet(?) WHERE {key} IS NOT NULL{clause} GROUP BY {key}"  # noqa: S608, E501 - identifiers quoted
        sql = (
            "SELECT coalesce(sum(n), 0), count(*), count(*) FILTER (WHERE n > 1), "  # noqa: S608
            f"coalesce(sum(n) FILTER (WHERE n > 1), 0) FROM ({inner}) g"
        )
        value = con.execute(
            sql,
            [files_param(files), *params],
        ).fetchone()
    finally:
        con.close()
    rows, distinct, copied, copied_rows = (int(v or 0) for v in (value or (0, 0, 0, 0)))
    return {
        "rows": rows,
        "row_keys": distinct,
        "keys_with_copies": copied,
        "rows_in_copied_keys": copied_rows,
    }


def row_keys(files: Sequence[Path], row_filter: Mapping[str, str] | None) -> pa.Table:
    """Every distinct row key (one column ``row_key``)."""
    con = connect()
    try:
        columns = top_level_columns(con, files)
        sql, params = _distinct_sql(
            files, f"{quote_ident(ROW_KEY, columns)} AS row_key", row_filter, columns
        )
        return con.execute(sql, params).to_arrow_table()
    finally:
        con.close()


def iterate_rows(
    files: Sequence[Path],
    field_map: Mapping[str, str],
    row_filter: Mapping[str, str] | None,
    recorded: pa.Table,
    batch_rows: int = 1000,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Unrecorded rows in row-key order: ``(row_key, {template field: value})``."""
    con = connect()
    try:
        columns = top_level_columns(con, files)
        selected = ", ".join(
            f"{quote_ident(column, columns)} AS {quote_ident(field, [field])}"
            for field, column in field_map.items()
        )
        key = quote_ident(ROW_KEY, columns)
        inner, params = _distinct_sql(files, f"{key} AS row_key, {selected}", row_filter, columns)
        con.register("recorded_keys", recorded)
        sql = (
            f"SELECT r.* FROM ({inner}) r ANTI JOIN recorded_keys k ON r.row_key = k.row_key "  # noqa: S608 - identifiers quoted
            "ORDER BY r.row_key"
        )
        reader = con.execute(sql, params).to_arrow_reader(batch_rows)
        fields = list(field_map)
        for batch in reader:
            keys = batch.column(0).to_pylist()
            values = [batch.column(i + 1).to_pylist() for i in range(len(fields))]
            for i, row_key in enumerate(keys):
                yield str(row_key), {f: values[j][i] for j, f in enumerate(fields)}
    finally:
        con.close()


def sample_rows(
    files: Sequence[Path],
    field_map: Mapping[str, str],
    row_filter: Mapping[str, str] | None,
    n: int,
    seed: int,
) -> list[tuple[str, dict[str, Any]]]:
    """A repeatable reservoir sample of ``n`` distinct rows (FTID 005 section 7.4)."""
    con = connect()
    try:
        columns = top_level_columns(con, files)
        selected = ", ".join(
            f"{quote_ident(column, columns)} AS {quote_ident(field, [field])}"
            for field, column in field_map.items()
        )
        key = quote_ident(ROW_KEY, columns)
        inner, params = _distinct_sql(files, f"{key} AS row_key, {selected}", row_filter, columns)
        sql = (
            f"SELECT * FROM ({inner}) USING SAMPLE reservoir({int(n)} ROWS) "  # noqa: S608 - integers only
            f"REPEATABLE ({int(seed)}) ORDER BY row_key"
        )
        table = con.execute(sql, params).to_arrow_table()
    finally:
        con.close()
    fields = list(field_map)
    keys = table.column("row_key").to_pylist()
    out = []
    for i, row_key in enumerate(keys):
        out.append((str(row_key), {f: table.column(f)[i].as_py() for f in fields}))
    return out


def empty_keys() -> pa.Table:
    return pa.table({"row_key": pa.array([], pa.string())})


def keys_table(keys: Sequence[str]) -> pa.Table:
    return pa.table({"row_key": pa.array(list(keys), pa.string())})
