"""Whole-split statistics of an imported source, before any version exists (PII run G2).

``dataworks_run_profile`` needs a version, and ``dataworks_preview_hf_dataset`` shows 100 sample
rows; the run computed row counts, label shares and lengths with pandas on downloaded Parquet.
This reads the source's own Parquet with DuckDB, which streams it: per split, the row count, the
nulls of every column, the share of each value of the columns asked for (top N and the rest), and
the length distribution of the text columns. A split over ``SOURCE_PROFILE_MAX_ROWS`` is refused
with the cap named, before anything is read. Nothing is written.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ...core.config import get_settings
from ...core.errors import AppError
from ...core.storage import resolve_under_data_dir
from ..duck import connect, files_param, quote_ident

QUANTILES = (0.0, 0.05, 0.25, 0.5, 0.75, 0.95, 1.0)


def _split_profile(
    paths: list[Path],
    value_columns: Sequence[str],
    text_columns: Sequence[str],
    top: int,
) -> dict[str, Any]:
    con = connect()
    try:
        source = "read_parquet(?, union_by_name=true)"
        param = [files_param(paths)]
        names = [
            d[0]
            for d in con.execute(f"SELECT * FROM {source} LIMIT 0", param).description  # noqa: S608
            or []
        ]
        unknown = sorted({c for c in [*value_columns, *text_columns] if c not in names})
        if unknown:
            raise AppError(
                f"The source has no column(s) {unknown}; it has {names}.",
                code="column_not_found",
                status_code=404,
                details={"columns": unknown},
            )
        nulls_sql = ", ".join(f"count(*) - count({quote_ident(c, names)})" for c in names)
        sql = f"SELECT count(*), {nulls_sql} FROM {source}"  # noqa: S608 - quoted identifiers
        counted = con.execute(sql, param).fetchone()
        assert counted is not None
        rows = int(counted[0])
        out: dict[str, Any] = {
            "rows": rows,
            "nulls": {c: int(n) for c, n in zip(names, counted[1:], strict=True)},
            "values": {},
            "lengths": {},
        }
        for column in value_columns:
            q = quote_ident(column, names)
            distinct = con.execute(
                f"SELECT count(DISTINCT {q}) FROM {source}", param  # noqa: S608
            ).fetchone()
            ranked = con.execute(
                f"SELECT CAST({q} AS VARCHAR) AS v, count(*) AS n FROM {source} "  # noqa: S608
                "GROUP BY 1 ORDER BY n DESC, v LIMIT ?",
                [*param, top],
            ).fetchall()
            shown = sum(int(n) for _, n in ranked)
            out["values"][column] = {
                "distinct": int(distinct[0]) if distinct else 0,
                "top": [
                    {"value": v, "rows": int(n), "share": (int(n) / rows) if rows else 0.0}
                    for v, n in ranked
                ],
                "other_rows": rows - shown,
                "other_share": ((rows - shown) / rows) if rows else 0.0,
            }
        for column in text_columns:
            q = quote_ident(column, names)
            length = f"length(CAST({q} AS VARCHAR))"
            quantiles = ", ".join(str(x) for x in QUANTILES)
            row = con.execute(
                f"SELECT quantile_cont({length}, [{quantiles}]), avg({length}), "  # noqa: S608
                f"count(*) FILTER (WHERE {q} IS NULL OR {length} = 0) FROM {source}",
                param,
            ).fetchone()
            assert row is not None
            values = list(row[0] or [])
            out["lengths"][column] = {
                "unit": "characters",
                "quantiles": {
                    f"p{int(x * 100)}": (float(v) if v is not None else None)
                    for x, v in zip(QUANTILES, values or [None] * len(QUANTILES), strict=True)
                },
                "mean": float(row[1]) if row[1] is not None else None,
                "empty_or_null": int(row[2]),
            }
        return out
    finally:
        con.close()


def profile(
    files: Sequence[Any],
    *,
    split: str | None,
    value_columns: Sequence[str],
    text_columns: Sequence[str],
    top: int,
) -> dict[str, Any]:
    """``{splits: {name: profile}, cap}`` for the source's files (``SourceFileInfo``)."""
    cap = get_settings().source_profile_max_rows
    by_split: dict[str, list[Any]] = {}
    for f in files:
        if split is None or f.split == split:
            by_split.setdefault(f.split, []).append(f)
    if not by_split:
        raise AppError(
            f"The source has no split {split!r}.", code="split_not_found", status_code=404
        )
    too_big = {name: sum(f.rows for f in fs) for name, fs in by_split.items()}
    too_big = {name: n for name, n in too_big.items() if n > cap}
    if too_big:
        raise AppError(
            f"Profiling reads a whole split, and is capped at {cap:,} rows "
            f"(SOURCE_PROFILE_MAX_ROWS); {too_big} exceed it. Profile one split, or build a "
            "sampled version and use dataworks_run_profile.",
            code="profile_too_large",
            status_code=413,
            details={"cap_rows": cap, "splits": too_big},
        )
    return {
        "cap_rows": cap,
        "splits": {
            name: _split_profile(
                [resolve_under_data_dir(f.path) for f in fs], value_columns, text_columns, top
            )
            for name, fs in sorted(by_split.items())
        },
    }
