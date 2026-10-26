"""Compare two versions (FR-002.38; FTID 002 section 7.3).

Per split: row counts and their difference; label balance for every label column present in either
version; the key set difference (added, removed, kept, and changed — matched through ``changed``
events in A's own lineage); the drop-log difference by operator and reason; and distributions on
SHARED bins — character length of each content column, numeric metadata histograms, top values of
categorical metadata. Counts are exact, computed by DuckDB over whole splits, and every figure names
its sample size. Reports are cached in ``dw_version_comparisons``: both versions are immutable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.errors import AppError, ConflictError
from ..core.storage import resolve_under_data_dir
from ..models.enums import ColumnRole, VersionState
from ..models.row_event import RowEvent
from ..models.version import Version, VersionComparison
from .duck import connect, files_param, quote_ident
from .lineage_service import lineage_executions
from .version_read_service import get_version_row

NUMERIC_BINS = 30
TOP_VALUES = 20


def _files(version: Version, split: str | None = None) -> list[Path]:
    return [
        resolve_under_data_dir(s["path"])
        for s in version.splits
        if split is None or s["name"] == split
    ]


def _label_columns(version: Version) -> list[str]:
    return [
        c
        for c, r in version.column_roles.items()
        if r == ColumnRole.METADATA and c.startswith("label")
    ]


def _types(con: Any, files: list[Path]) -> dict[str, str]:
    if not files:
        return {}
    rows = con.execute("DESCRIBE SELECT * FROM read_parquet(?)", [files_param(files)]).fetchall()
    return {r[0]: str(r[1]) for r in rows}


def _length_expr(column: str, type_name: str, names: list[str]) -> str | None:
    ident = quote_ident(column, names)
    if type_name == "VARCHAR":
        return f"length({ident})"
    if type_name.startswith("STRUCT") and type_name.endswith("[]"):
        return f"list_sum(list_transform({ident}, m -> length(CAST(m.content AS VARCHAR))))"
    return None


def _numeric(type_name: str) -> bool:
    return type_name in {
        "BIGINT",
        "INTEGER",
        "SMALLINT",
        "TINYINT",
        "DOUBLE",
        "FLOAT",
        "DECIMAL",
        "HUGEINT",
    }


def _histogram(con: Any, expr: str, files_a: list[Path], files_b: list[Path]) -> dict[str, Any]:
    """Equal-width bins over the union's range, identical for both versions."""
    union = f"SELECT {expr} AS x FROM read_parquet(?) UNION ALL SELECT {expr} AS x FROM read_parquet(?)"  # noqa: S608
    low, high = con.execute(
        f"SELECT min(x), max(x) FROM ({union})",  # noqa: S608
        [files_param(files_a), files_param(files_b)],
    ).fetchone()
    if low is None:
        return {"bins": [], "a": [], "b": [], "n_a": 0, "n_b": 0}
    low, high = float(low), float(high)
    width = (high - low) / NUMERIC_BINS if high > low else 1.0
    edges = [low + i * width for i in range(NUMERIC_BINS + 1)]

    def counts(files: list[Path]) -> tuple[list[int], int]:
        if not files:
            return [0] * NUMERIC_BINS, 0
        rows = con.execute(
            f"SELECT least(CAST(floor(({expr} - ?) / ?) AS INTEGER), {NUMERIC_BINS - 1}) AS b, "  # noqa: S608
            f"count(*) FROM read_parquet(?) WHERE {expr} IS NOT NULL GROUP BY b",
            [low, width, files_param(files)],
        ).fetchall()
        out = [0] * NUMERIC_BINS
        for b, n in rows:
            out[int(b)] += int(n)
        return out, sum(out)

    a, n_a = counts(files_a)
    b, n_b = counts(files_b)
    return {"bins": edges, "a": a, "b": b, "n_a": n_a, "n_b": n_b}


def _top_values(con: Any, ident: str, files_a: list[Path], files_b: list[Path]) -> dict[str, Any]:
    union = (
        f"SELECT CAST({ident} AS VARCHAR) AS v FROM read_parquet(?) UNION ALL "  # noqa: S608
        f"SELECT CAST({ident} AS VARCHAR) AS v FROM read_parquet(?)"
    )
    top = [
        r[0]
        for r in con.execute(
            f"SELECT v, count(*) AS n FROM ({union}) GROUP BY v ORDER BY n DESC, v LIMIT ?",  # noqa: S608
            [files_param(files_a), files_param(files_b), TOP_VALUES],
        ).fetchall()
    ]

    def counts(files: list[Path]) -> tuple[dict[str, int], int]:
        if not files:
            return {}, 0
        rows = con.execute(
            f"SELECT CAST({ident} AS VARCHAR) AS v, count(*) FROM read_parquet(?) GROUP BY v",  # noqa: S608
            [files_param(files)],
        ).fetchall()
        out: dict[str, int] = {}
        for v, n in rows:
            key = v if v in top else "other"
            out[str(key)] = out.get(str(key), 0) + int(n)
        return out, sum(out.values())

    a, n_a = counts(files_a)
    b, n_b = counts(files_b)
    return {"values": [str(v) for v in top] + ["other"], "a": a, "b": b, "n_a": n_a, "n_b": n_b}


def _drop_diff(a: Version, b: Version) -> list[dict[str, Any]]:
    def flatten(version: Version) -> dict[tuple[str, str], int]:
        out: dict[tuple[str, str], int] = {}
        for step in version.drop_summary:
            for reason in step.get("reasons", []):
                key = (str(step["operator"]), str(reason["reason_code"]))
                out[key] = out.get(key, 0) + int(reason["count"])
        return out

    fa, fb = flatten(a), flatten(b)
    return [
        {
            "operator": op,
            "reason_code": code,
            "a": fa.get((op, code), 0),
            "b": fb.get((op, code), 0),
            "difference": fa.get((op, code), 0) - fb.get((op, code), 0),
        }
        for op, code in sorted(set(fa) | set(fb))
    ]


async def _changed_pairs(
    db: AsyncSession, a: Version, b_keys: set[str], a_keys: set[str]
) -> set[tuple[str, str]]:
    own = [c.execution.id for c in await lineage_executions(db, a) if c.version_id == a.id]
    if not own:
        return set()
    rows = (
        await db.execute(
            select(RowEvent.row_key, RowEvent.new_row_key).where(
                RowEvent.step_execution_id.in_(own), RowEvent.kind == "changed"
            )
        )
    ).all()
    out: set[tuple[str, str]] = set()
    for old, new in rows:
        o, n = old.hex(), new.hex() if new else None
        if n and o != n and o in b_keys and n in a_keys:
            out.add((o, n))
    return out


async def compare(db: AsyncSession, version_id: str, other_id: str | None) -> dict[str, Any]:
    a = await get_version_row(db, version_id)
    if other_id is None:
        if a.parent_version_id is None:
            raise AppError(
                f"Version {a.number} has no parent to compare with; choose another version.",
                code="no_parent",
                status_code=409,
            )
        other_id = a.parent_version_id
    b = await get_version_row(db, other_id)
    for version in (a, b):
        if version.state == VersionState.DELETED:
            raise ConflictError(
                f"Version {version.number} was deleted; its rows are gone, so it cannot be compared.",
                code="version_deleted",
                details={"version_id": version.id},
            )
    if a.rowkey_scheme != b.rowkey_scheme:
        raise ConflictError(
            f"The versions use different row-key schemes ({a.rowkey_scheme} and "
            f"{b.rowkey_scheme}); keys of two schemes are never compared.",
            code="rowkey_scheme_mismatch",
            details={"schemes": [a.rowkey_scheme, b.rowkey_scheme]},
        )
    cached = await db.get(VersionComparison, (a.id, b.id))
    if cached is not None:
        return dict(cached.report) | {"cached": True}
    report = await _build(db, a, b)
    await db.execute(
        pg_insert(VersionComparison)
        .values(version_a=a.id, version_b=b.id, report=report)
        .on_conflict_do_nothing()
    )
    await db.commit()
    return report | {"cached": False}


async def _build(db: AsyncSession, a: Version, b: Version) -> dict[str, Any]:
    con = connect()
    try:
        files_a, files_b = _files(a), _files(b)
        keys_a = (
            {
                r[0]
                for r in con.execute(
                    "SELECT DISTINCT _dw_row_key FROM read_parquet(?)", [files_param(files_a)]
                ).fetchall()
            }
            if files_a
            else set()
        )
        keys_b = (
            {
                r[0]
                for r in con.execute(
                    "SELECT DISTINCT _dw_row_key FROM read_parquet(?)", [files_param(files_b)]
                ).fetchall()
            }
            if files_b
            else set()
        )
        changed = await _changed_pairs(db, a, keys_b, keys_a)
        changed_from = {o for o, _ in changed}
        changed_to = {n for _, n in changed}
        keys = {
            "added": len(keys_a - keys_b - changed_to),
            "removed": len(keys_b - keys_a - changed_from),
            "kept": len(keys_a & keys_b),
            "changed": len(changed),
            "n_a": len(keys_a),
            "n_b": len(keys_b),
        }
        splits = []
        for name in dict.fromkeys([s["name"] for s in a.splits] + [s["name"] for s in b.splits]):
            rows_a = next((s["rows"] for s in a.splits if s["name"] == name), 0)
            rows_b = next((s["rows"] for s in b.splits if s["name"] == name), 0)
            balance: dict[str, Any] = {}
            for column in sorted(set(_label_columns(a)) | set(_label_columns(b))):
                per: dict[str, dict[str, int]] = {}
                for side, version in (("a", a), ("b", b)):
                    files = _files(version, name)
                    names = list(_types(con, files))
                    if not files or column not in names:
                        per[side] = {}
                        continue
                    ident = quote_ident(column, names)
                    per[side] = {
                        str(v): int(n)
                        for v, n in con.execute(
                            f"SELECT CAST({ident} AS VARCHAR), count(*) FROM read_parquet(?) GROUP BY 1",  # noqa: S608
                            [files_param(files)],
                        ).fetchall()
                    }
                balance[column] = per
            splits.append(
                {
                    "split": name,
                    "rows_a": rows_a,
                    "rows_b": rows_b,
                    "difference": rows_a - rows_b,
                    "label_balance": balance,
                }
            )
        types = _types(con, files_a) | _types(con, files_b)
        names = list(types)
        types_a, types_b = _types(con, files_a), _types(con, files_b)
        distributions: list[dict[str, Any]] = []
        roles = dict(b.column_roles) | dict(a.column_roles)
        for column, role in sorted(roles.items()):
            if role == ColumnRole.SYSTEM or column not in types_a or column not in types_b:
                continue
            type_name = types[column]
            if role == ColumnRole.CONTENT:
                expr = _length_expr(column, type_name, names)
                if expr:
                    distributions.append(
                        {
                            "column": column,
                            "kind": "length",
                            **_histogram(con, expr, files_a, files_b),
                        }
                    )
            elif _numeric(type_name):
                distributions.append(
                    {
                        "column": column,
                        "kind": "numeric",
                        **_histogram(con, quote_ident(column, names), files_a, files_b),
                    }
                )
            elif type_name in {"VARCHAR", "BOOLEAN"}:
                distributions.append(
                    {
                        "column": column,
                        "kind": "categorical",
                        **_top_values(con, quote_ident(column, names), files_a, files_b),
                    }
                )
    finally:
        con.close()
    return {
        "version_a": {"id": a.id, "number": a.number, "total_rows": a.total_rows},
        "version_b": {"id": b.id, "number": b.number, "total_rows": b.total_rows},
        "splits": splits,
        "keys": keys,
        "drop_log": _drop_diff(a, b),
        "distributions": distributions,
    }
