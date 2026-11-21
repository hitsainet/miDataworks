"""Reading a finished label run's published labels inside an operator (no database session)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pyarrow.parquet as pq

from ....core.storage import resolve_under_data_dir
from ...context import RunContext
from ...errors import OperatorError


def bound(ctx: RunContext, run_id: str) -> None:
    """Refuse a run the build did not bind (002 binds and checks it completed).

    Fails CLOSED in a build: until 2026-10-07 an empty binding list passed (``if ctx.bindings and
    ...``), so a recipe step naming a label run its build never bound read it unchecked — 002's
    completeness check never saw that run. Only a preview (no step execution) may read an unbound
    run, as before.
    """
    ids = {str(b.get("id")) for b in ctx.bindings if b.get("kind") == "label_run"}
    if (ctx.bindings or ctx.step_execution_id is not None) and run_id not in ids:
        raise OperatorError(
            "label_run_not_bound",
            f"Label run {run_id} is not bound to this build; bind it in the recipe.",
            {"label_run_id": run_id},
        )


def published_labels(run_id: str) -> dict[str, dict[str, Any]]:
    """row key -> {outcome, provisional, parsed_value} from the run's ``labels.parquet``."""
    path = resolve_under_data_dir("runs", run_id, "labels.parquet")
    if not path.is_file():
        raise OperatorError(
            "label_run_not_published",
            f"Label run {run_id} has no published labels; bind a completed run.",
            {"label_run_id": run_id},
        )
    table = pq.read_table(path, columns=["row_key", "outcome", "provisional", "parsed_value"])
    out: dict[str, dict[str, Any]] = {}
    for key, outcome, provisional, parsed in zip(
        table.column("row_key").to_pylist(),
        table.column("outcome").to_pylist(),
        table.column("provisional").to_pylist(),
        table.column("parsed_value").to_pylist(),
        strict=True,
    ):
        out[str(key)] = {
            "outcome": outcome,
            "provisional": bool(provisional),
            "parsed_value": json.loads(parsed) if parsed is not None else None,
        }
    return out


def value_key(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def reference_from_column(
    rows: Mapping[str, object], positive: list[Any], negative: list[Any]
) -> dict[str, bool]:
    pos = {value_key(v) for v in positive}
    neg = {value_key(v) for v in negative}
    out: dict[str, bool] = {}
    for key, raw in rows.items():
        if raw is None:
            continue
        k = value_key(raw)
        if k in pos:
            out[key] = True
        elif k in neg:
            out[key] = False
    return out
