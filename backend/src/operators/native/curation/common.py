"""Shared pieces for feature 004's operators: manifests, reading a whole input, refusals.

Every operator here builds its manifest through :func:`manifest`, reads a dataset-scope input
through :func:`read_all` (the executor's ``ctx.input_reader``; never a path), and refuses a
semantic problem with :func:`refuse`, a ``StepFailed`` carrying 004's stable code so feature 002's
callback, the preview panel and the API all show the same reason (FTID 004 §12).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Hashable, Mapping, Sequence
from typing import Any

import pyarrow as pa

from ....rules.next_step import next_for
from ....services.curation.components import IdHolders, holders_of, unrelated_across_runs
from ...context import RunContext
from ...errors import StepFailed
from ...manifest import ColumnSpec, Kind, OperatorManifest, ResourceSpec, Scope, ThresholdSpec

PROVIDER_VERSION = "midataworks-004"
SYSTEM_PREFIX = "_dw_"
CARRIER_ID = "carrier_id"
PAIR_ID = "pair_id"
SIBLING_VARIANT = "sibling_variant"
GENERATION_RUN = "generation_run_id"
#: The plan columns no later step rewrites, so all of a plan row's carriers and siblings agree on
#: them (``slot_siblings@1`` changes ``kind``, ``form`` and ``length_band`` on a sibling; a run writes
#: its own ``generator`` and ``slot``). ``planned_*`` columns count too.
PLAN_CELL_COLUMNS = ("genre", "placement", "framing")


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
    check: bool = False,
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
        check=check,
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


def plan_cells(table: pa.Table) -> list[Hashable] | None:
    """Each row's plan cell (:data:`PLAN_CELL_COLUMNS`), or None when the rows have no plan columns."""
    names = table.schema.names
    columns = [c for c in names if c in PLAN_CELL_COLUMNS or c.startswith("planned_")]
    if not columns:
        return None
    return list(zip(*(text_column(table, c) for c in sorted(columns)), strict=True))


def carrier_collision(
    column: str, unrelated: Mapping[str, IdHolders], *, so_far: bool = False
) -> StepFailed:
    """``carrier_id_collision``: the ids of ``column`` that unrelated rows of more than one
    generation run hold. ``so_far`` when the operator has read only part of its input (row scope),
    so the counts are a floor; ``split@1`` reads it all."""
    by_run: Counter[str] = Counter()
    for holder in unrelated.values():
        by_run.update(holder.rows_by_run)
    rows = sum(by_run.values())
    named = ", ".join(f"{run} ({count:,})" for run, count in sorted(by_run.items()))
    return refuse(
        "carrier_id_collision",
        f"{'At least ' if so_far else ''}{len(unrelated):,} {column} value(s) belong to rows of "
        f"more than one generation run that were not planned as one row: {rows:,} rows, by run "
        f"{named}. Two carrier plans over overlapping index ranges make the same ids, and grouped "
        "on this id those rows would become one unit. Give one run's carriers their own ids in a "
        "step before this one, or plan them over an index range no other run uses.",
        {
            "column": column,
            "ids": len(unrelated),
            "rows": rows,
            "rows_by_run": dict(sorted(by_run.items())),
            "runs": sorted(by_run),
            "examples": sorted(unrelated)[:5],
            "partial": so_far,
            "next": next_for(
                "dataworks_revise_recipe",
                {},
                "re-id one run's carriers before this step, so ids are unique across runs",
            ),
        },
    )


def refuse_carrier_collisions(table: pa.Table, group_columns: Sequence[str]) -> None:
    """Refuse grouping on an id that unrelated rows of two generation runs hold (dry run,
    2026-11-01: 2,659 rows of a merged version shared an id with an unrelated row). ``carrier_id``,
    and ``pair_id`` where ``slot_siblings@1`` wrote it from ``carrier_id``. Another ``pair_id`` (a
    Humicroedit headline, a minimal pair) may rightly span runs, and a table with no runs has nothing
    to collide."""
    names = table.schema.names
    if GENERATION_RUN not in names:
        return
    runs = text_column(table, GENERATION_RUN)
    cells = plan_cells(table)
    for column in group_columns:
        if column == CARRIER_ID or (column == PAIR_ID and SIBLING_VARIANT in names):
            unrelated = unrelated_across_runs(holders_of(text_column(table, column), runs, cells))
            if unrelated:
                raise carrier_collision(column, unrelated)
