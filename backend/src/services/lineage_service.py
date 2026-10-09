"""Lineage, row history ("Why did this row leave?"), drop log, events and row pages.

FR-002.26, 002.27, 002.33; FTDD 002 section 5.4; FTID 002 section 3.8.

Row history walks the version's lineage — its own step executions, then each input version's,
recursively — and follows ``changed`` events across keys in both directions: backward through
``new_row_key`` to the key the row entered with, forward through ``row_key`` to where it left. Row
text never leaves Parquet; PostgreSQL holds keys and reasons (FR-002.32).
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..core.errors import AppError, ConflictError
from ..core.storage import resolve_under_data_dir
from ..models.enums import ColumnRole, VersionState
from ..models.row_event import RowEvent
from ..models.source import Source
from ..models.step_execution import StepExecution
from ..models.version import Version, VersionInput, VersionStep
from .duck import FILTER_OPS, connect, files_param, quote_ident, where_clause
from .version_read_service import get_version_row

MIN_PREFIX = 12
_HEX = re.compile(r"^[0-9a-f]+$")


def _refuse_deleted(version: Version) -> None:
    if version.state == VersionState.DELETED:
        raise ConflictError(
            f"Version {version.number} was deleted; its rows are gone. Its manifest, counts and "
            "events remain.",
            code="version_deleted",
            details={"version_id": version.id},
        )


def split_paths(version: Version) -> list[Path]:
    return [resolve_under_data_dir(s["path"]) for s in version.splits]


def _json_safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    return value


# --------------------------------------------------------------------------------------------
# Lineage
# --------------------------------------------------------------------------------------------


@dataclass
class LineageExecution:
    execution: StepExecution
    version_id: str
    version_number: int
    step_index: int
    reused: bool


async def lineage_executions(db: AsyncSession, version: Version) -> list[LineageExecution]:
    """The version's executions (latest step first), then its input versions', recursively.

    An execution reused by several versions is attributed to the first version that reaches it,
    so a drop in one of this version's own steps reads "dropped in this version".
    """
    seen_versions: set[str] = set()
    seen_executions: set[str] = set()
    out: list[LineageExecution] = []
    queue = [version]
    while queue:
        current = queue.pop(0)
        if current.id in seen_versions:
            continue
        seen_versions.add(current.id)
        rows = (
            await db.execute(
                select(VersionStep, StepExecution)
                .join(StepExecution, StepExecution.id == VersionStep.step_execution_id)
                .where(VersionStep.version_id == current.id)
                .order_by(VersionStep.step_index.desc())
            )
        ).all()
        for step, execution in rows:
            if execution.id in seen_executions:
                continue
            seen_executions.add(execution.id)
            out.append(
                LineageExecution(
                    execution, current.id, current.number, step.step_index, step.reused
                )
            )
        parents = (
            await db.execute(
                select(VersionInput.input_version_id)
                .where(
                    VersionInput.version_id == current.id,
                    VersionInput.input_version_id.is_not(None),
                )
                .order_by(VersionInput.position)
            )
        ).scalars()
        for parent_id in list(parents):
            parent = await db.get(Version, parent_id)
            if parent is not None:
                queue.append(parent)
    return out


async def lineage(db: AsyncSession, version_id: str) -> dict[str, Any]:
    version = await get_version_row(db, version_id)
    inputs: list[dict[str, Any]] = []
    for item in version.inputs:
        entry = dict(item)
        if item["kind"] == "source":
            source = await db.get(Source, item["source_id"])
            entry["display_name"] = source.display_name if source else None
            entry["licence_display"] = source.licence_display if source else None
        else:
            parent = await db.get(Version, item["version_id"])
            entry["number"] = parent.number if parent else None
            entry["state"] = parent.state if parent else None
        inputs.append(entry)
    children = (
        await db.execute(
            select(Version.id, Version.number, Version.state)
            .join(VersionInput, VersionInput.version_id == Version.id)
            .where(VersionInput.input_version_id == version.id)
            .order_by(Version.number)
        )
    ).all()
    steps = (
        await db.execute(
            select(VersionStep, StepExecution)
            .join(StepExecution, StepExecution.id == VersionStep.step_execution_id)
            .where(VersionStep.version_id == version.id)
            .order_by(VersionStep.step_index)
        )
    ).all()
    return {
        "version_id": version.id,
        "inputs": inputs,
        "parent_version_id": version.parent_version_id,
        "children": [{"version_id": c, "number": n, "state": s} for c, n, s in children],
        "steps": [
            {
                "index": s.step_index,
                "execution_id": e.id,
                "kind": e.kind,
                "operator": e.operator_name,
                "operator_version": e.operator_version,
                "identity": e.identity_digest,
                "reused": s.reused,
                "step_seed": e.step_seed,
                "rows_in": e.rows_in,
                "rows_dropped": e.rows_dropped,
                "rows_changed": e.rows_changed,
                "rows_added": e.rows_added,
            }
            for s, e in steps
        ],
        "held_out_origin_version_id": version.held_out_origin_version_id,
        "recipe_hash": version.recipe_hash,
        "recipe_revision_id": version.recipe_revision_id,
        "seed": version.seed,
        "rowkey_scheme": version.rowkey_scheme,
        "splits": version.splits,
    }


# --------------------------------------------------------------------------------------------
# Row history (FR-002.26)
# --------------------------------------------------------------------------------------------


def _row_dict(row: Any) -> dict[str, Any]:
    return {
        "row_key": row.row_key.hex(),
        "occurrence": row.occurrence,
        "new_row_key": row.new_row_key.hex() if row.new_row_key else None,
        "kind": row.kind,
        "reason_code": row.reason_code,
        "reason": row.reason,
        "statistic_name": row.statistic_name,
        "statistic_value": row.statistic_value,
        "statistic_text": row.statistic_text,
        "threshold": row.threshold,
        "parent_keys": [k.hex() for k in row.parent_keys] if row.parent_keys else None,
        "execution_id": row.step_execution_id,
    }


def _present(version: Version, key: str) -> list[dict[str, Any]]:
    # A deleted version has no files; neither does one whose recipe dropped every row, and
    # DuckDB refuses read_parquet([]) — that version is where row history matters most.
    if version.state == VersionState.DELETED or not version.splits:
        return []
    con = connect()
    try:
        rows = con.execute(
            "SELECT _dw_split, _dw_occurrence, _dw_source_id, _dw_source_locator, _dw_origin, "
            "_dw_parent_keys FROM read_parquet(?) WHERE _dw_row_key = ? ORDER BY _dw_occurrence",
            [files_param(split_paths(version)), key],
        ).fetchall()
    finally:
        con.close()
    return [
        {
            "version_id": version.id,
            "split": r[0],
            "occurrence": r[1],
            "source_id": r[2],
            "source_locator": r[3],
            "origin": r[4],
            "parent_keys": r[5],
        }
        for r in rows
    ]


@dataclass
class _Walk:
    by_execution: dict[str, LineageExecution]
    events: list[dict[str, Any]] = field(default_factory=list)


async def _events_for(
    db: AsyncSession, keys: set[str], executions: list[str]
) -> list[dict[str, Any]]:
    if not keys or not executions:
        return []
    raw = [bytes.fromhex(k) for k in keys]
    rows = (
        await db.execute(
            select(RowEvent).where(
                RowEvent.step_execution_id.in_(executions),
                or_(RowEvent.row_key.in_(raw), RowEvent.new_row_key.in_(raw)),
            )
        )
    ).scalars()
    return [_row_dict(r) for r in rows]


async def _origin_from_assembly(
    db: AsyncSession, chain: list[LineageExecution], key: str
) -> dict[str, Any] | None:
    for item in chain:
        if item.execution.kind != "assemble":
            continue
        directory = resolve_under_data_dir(item.execution.output_dir)
        parts = sorted(directory.glob("part-*.parquet"))
        if not parts:
            continue
        con = connect()
        try:
            row = con.execute(
                "SELECT _dw_source_id, _dw_source_locator, _dw_origin, _dw_parent_keys FROM "
                "read_parquet(?) WHERE _dw_row_key = ? LIMIT 1",
                [files_param(parts), key],
            ).fetchone()
        finally:
            con.close()
        if row is not None:
            if row[2] == "generated":
                return {"generated": True, "parent_keys": row[3]}
            return {"source_id": row[0], "source_locator": row[1]}
    return None


def _describe(walk: _Walk, event: dict[str, Any]) -> dict[str, Any]:
    where = walk.by_execution[event["execution_id"]]
    return {
        "version_id": where.version_id,
        "version_number": where.version_number,
        "step_index": where.step_index,
        "operator": where.execution.operator_name,
        "operator_version": where.execution.operator_version,
        "kind": event["kind"],
        "from_key": event["row_key"],
        "to_key": event["new_row_key"],
        "reason_code": event["reason_code"],
        "reason": event["reason"],
        "statistic_name": event["statistic_name"],
        "statistic_value": event["statistic_value"],
        "statistic_text": event["statistic_text"],
        "threshold": event["threshold"],
    }


async def row_history(db: AsyncSession, version: Version, key: str) -> dict[str, Any]:
    chain = await lineage_executions(db, version)
    walk = _Walk({c.execution.id: c for c in chain})
    executions = list(walk.by_execution)

    trail: list[dict[str, Any]] = []
    seen_keys = {key}
    # backward: which changes produced this key?
    frontier = {key}
    origin_key = key
    while frontier:
        events = [
            e
            for e in await _events_for(db, frontier, executions)
            if e["new_row_key"] in frontier
            and e["kind"] == "changed"
            and e["row_key"] != e["new_row_key"]
        ]
        frontier = set()
        for event in events:
            trail.append(_describe(walk, event))
            if event["row_key"] not in seen_keys:
                seen_keys.add(event["row_key"])
                frontier.add(event["row_key"])
                origin_key = event["row_key"]
    # forward: where did this key go?
    dropped: dict[str, Any] | None = None
    frontier = {key}
    while frontier:
        events = [
            e for e in await _events_for(db, frontier, executions) if e["row_key"] in frontier
        ]
        frontier = set()
        for event in events:
            described = _describe(walk, event)
            if event["kind"] == "dropped":
                dropped = dropped or described
                trail.append(described)
            elif event["kind"] == "changed":
                if described not in trail:
                    trail.append(described)
                new = event["new_row_key"]
                if new and new not in seen_keys:
                    seen_keys.add(new)
                    frontier.add(new)
            elif event["kind"] in {"split_assigned", "added"}:
                trail.append(described)
    trail.sort(key=lambda t: (t["version_number"], t["step_index"]))
    present = _present(version, key)
    if present:
        status = "present"
    elif dropped is not None:
        status = "dropped"
    else:
        status = "not_found"
    origin: dict[str, Any] | None = None
    if present:
        first = present[0]
        origin = (
            {"generated": True, "parent_keys": first["parent_keys"]}
            if first["origin"] == "generated"
            else {"source_id": first["source_id"], "source_locator": first["source_locator"]}
        )
    if origin is None or (origin_key != key):
        origin = await _origin_from_assembly(db, list(reversed(chain)), origin_key) or origin
    searched = sorted({c.version_id for c in chain} | {version.id})
    return {
        "row_key": key,
        "status": status,
        "present_in": present,
        "dropped_at": dropped,
        "trail": trail,
        "origin": origin,
        "searched": searched,
        "version_deleted": version.state == VersionState.DELETED,
    }


async def resolve_key(db: AsyncSession, version: Version, value: str) -> str:
    """A full key, or a unique prefix of at least 12 hex characters."""
    value = value.strip().lower()
    if len(value) < MIN_PREFIX or not _HEX.fullmatch(value):
        raise AppError(
            f"A row key prefix needs at least {MIN_PREFIX} hex characters.",
            code="row_key_prefix_too_short",
            status_code=422,
        )
    if len(value) == 64:
        return value
    candidates: set[str] = set()
    if version.state != VersionState.DELETED and version.splits:
        con = connect()
        try:
            for (k,) in con.execute(
                "SELECT DISTINCT _dw_row_key FROM read_parquet(?) WHERE starts_with(_dw_row_key, ?) "
                "LIMIT 3",
                [files_param(split_paths(version)), value],
            ).fetchall():
                candidates.add(k)
        finally:
            con.close()
    chain = await lineage_executions(db, version)
    ids = [c.execution.id for c in chain]
    if ids and len(candidates) < 2:
        rows = (
            await db.execute(
                select(RowEvent.row_key, RowEvent.new_row_key).where(
                    RowEvent.step_execution_id.in_(ids)
                )
            )
        ).all()
        for a, b in rows:
            for k in (a, b):
                if k is not None and k.hex().startswith(value):
                    candidates.add(k.hex())
    if len(candidates) > 1:
        raise ConflictError(
            f"The prefix {value} matches more than one row key; give more characters.",
            code="row_key_ambiguous",
            details={"candidates": sorted(candidates)[:5]},
        )
    return candidates.pop() if candidates else value


async def text_search(db: AsyncSession, version: Version, query: str) -> list[str]:
    """Row keys whose content contains ``query``, over the version and its assembly output."""
    limit = get_settings().row_history_text_matches
    content = [c for c, r in version.column_roles.items() if r == ColumnRole.CONTENT]
    files: list[list[Path]] = []
    if version.state != VersionState.DELETED:
        files.append(split_paths(version))
    for item in await lineage_executions(db, version):
        if item.execution.kind == "assemble" and item.version_id == version.id:
            files.append(
                sorted(resolve_under_data_dir(item.execution.output_dir).glob("part-*.parquet"))
            )
    keys: list[str] = []
    for group in files:
        if not group:
            continue
        con = connect()
        try:
            names = [
                d[0]
                for d in con.execute(
                    "SELECT * FROM read_parquet(?) LIMIT 0", [files_param(group)]
                ).description
                or []
            ]
            usable = [quote_ident(c, names) for c in content if c in names]
            if not usable:
                continue
            condition = " OR ".join(FILTER_OPS["contains"].format(c=c) for c in usable)
            sql = (
                "SELECT DISTINCT _dw_row_key FROM read_parquet(?) WHERE "  # noqa: S608
                f"{condition} LIMIT ?"
            )
            params: list[Any] = [files_param(group), *([query] * len(usable)), limit]
            for (k,) in con.execute(sql, params).fetchall():
                if k not in keys:
                    keys.append(k)
        finally:
            con.close()
    return keys[:limit]


async def history(
    db: AsyncSession, version_id: str, row_key: str | None, query: str | None
) -> dict[str, Any]:
    version = await get_version_row(db, version_id)
    if row_key:
        key = await resolve_key(db, version, row_key)
        return {"query": None, "results": [await row_history(db, version, key)]}
    if not query:
        raise AppError(
            "Give a row_key or a text query q.", code="history_query_missing", status_code=422
        )
    keys = await text_search(db, version, query)
    results = [await row_history(db, version, k) for k in keys]
    return {"query": query, "results": results, "searched": [version.id] if not results else None}


# --------------------------------------------------------------------------------------------
# Drop log, events, rows
# --------------------------------------------------------------------------------------------


async def drop_log(db: AsyncSession, version_id: str) -> dict[str, Any]:
    version = await get_version_row(db, version_id)
    return {
        "version_id": version.id,
        "total_rows": version.total_rows,
        "steps": version.drop_summary,
    }


async def events_page(
    db: AsyncSession,
    version_id: str,
    *,
    step_index: int | None,
    kind: str | None,
    reason_code: str | None,
    row_key: str | None,
    page: int,
    limit: int,
) -> dict[str, Any]:
    version = await get_version_row(db, version_id)
    steps = (
        await db.execute(select(VersionStep).where(VersionStep.version_id == version.id))
    ).scalars()
    index_of = {s.step_execution_id: s.step_index for s in steps}
    ids = [e for e, i in index_of.items() if step_index is None or i == step_index]
    if not ids:
        return {"items": [], "total": 0, "page": page, "limit": limit}
    query = select(RowEvent).where(RowEvent.step_execution_id.in_(ids))
    if kind:
        query = query.where(RowEvent.kind == kind)
    if reason_code:
        query = query.where(RowEvent.reason_code == reason_code)
    if row_key:
        if len(row_key) != 64 or not _HEX.fullmatch(row_key):
            raise AppError(
                "row_key must be 64 hex characters.", code="row_key_invalid", status_code=422
            )
        raw = bytes.fromhex(row_key)
        query = query.where(or_(RowEvent.row_key == raw, RowEvent.new_row_key == raw))
    from sqlalchemy import func

    total = int((await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one())
    rows = (
        await db.execute(
            query.order_by(RowEvent.step_execution_id, RowEvent.seq)
            .offset((page - 1) * limit)
            .limit(limit)
        )
    ).scalars()
    items = []
    for r in rows:
        item = _row_dict(r)
        item["step_index"] = index_of[r.step_execution_id]
        items.append(item)
    return {"items": items, "total": total, "page": page, "limit": limit}


def parse_filters(where: str | None) -> list[dict[str, Any]]:
    if not where:
        return []
    try:
        parsed = json.loads(where)
    except json.JSONDecodeError:
        raise AppError(
            "where must be a JSON list of {column, op, value}; free SQL is not accepted.",
            code="filter_invalid",
            status_code=422,
        ) from None
    if not isinstance(parsed, list) or not all(isinstance(f, dict) for f in parsed):
        raise AppError(
            "where must be a JSON list of filters.", code="filter_invalid", status_code=422
        )
    return parsed


async def rows_page(
    db: AsyncSession,
    version_id: str,
    *,
    split: str | None,
    query: str | None,
    where: str | None,
    page: int,
    limit: int,
) -> dict[str, Any]:
    version = await get_version_row(db, version_id)
    _refuse_deleted(version)
    files = split_paths(version)
    if split is not None:
        chosen = [s for s in version.splits if s["name"] == split]
        if not chosen:
            raise AppError(
                f"Version {version.number} has no split {split!r}.",
                code="split_not_found",
                status_code=404,
            )
        files = [resolve_under_data_dir(chosen[0]["path"])]
    filters = parse_filters(where)
    if not files:  # every row was dropped: an empty page, not a DuckDB error
        return {"items": [], "total": 0, "page": page, "limit": limit, "columns": []}
    con = connect()
    try:
        names = [
            d[0]
            for d in con.execute(
                "SELECT * FROM read_parquet(?) LIMIT 0", [files_param(files)]
            ).description
            or []
        ]
        clause, params = where_clause(filters, names)
        if query:
            content = [
                quote_ident(c, names)
                for c, r in version.column_roles.items()
                if r == ColumnRole.CONTENT and c in names
            ]
            text_condition = (
                " OR ".join(FILTER_OPS["contains"].format(c=c) for c in content) or "false"
            )
            clause = (clause + " AND " if clause else " WHERE ") + f"({text_condition})"
            params.extend([query] * len(content))
        total_sql = f"SELECT count(*) FROM read_parquet(?){clause}"  # noqa: S608
        total = int(con.execute(total_sql, [files_param(files), *params]).fetchone()[0])  # type: ignore[index]
        page_sql = f"SELECT * FROM read_parquet(?){clause} LIMIT ? OFFSET ?"  # noqa: S608
        table = con.execute(
            page_sql, [files_param(files), *params, limit, (page - 1) * limit]
        ).to_arrow_table()
    finally:
        con.close()
    return {
        "items": [_json_safe(r) for r in table.to_pylist()],
        "total": total,
        "page": page,
        "limit": limit,
        "columns": names,
    }
