"""``split``: assign every row to a named split, stratified, groups kept whole (FR-004.47–004.50).

A ``selector`` with the ``assign_split`` effect (003 ``effects.ALLOWED``): it writes ``_dw_split``,
emits one ``split_assigned`` event per row whose split changes, drops and changes nothing, and
declares ``split_roles`` in its report, which 003's executor copies into ``meta.json`` for 002's
finalize. The plan is the pure ``services/curation/split_plan.plan_split``.

Parameters are parallel arrays (``split_names``, ``split_fractions``, ``held_out``) rather than
FTDD §6.5's list of objects, because 003's supported schema subset has no arrays of objects (the
code wins; recorded). ``stratify_by`` defaults are resolved by the caller from the draft (the label
column plus an upstream ``cell_balancer``'s column, FR-004.41); the operator never guesses them.
Mode ``keep_source`` keeps the rows' existing splits and only declares their roles (Humicroedit's
upstream train/validation/test).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pyarrow as pa

from ....services.curation.binning import NULL_LABEL
from ....services.curation.split_plan import SplitRefusal, SplitSpec, check_specs, plan_split
from ...context import RunContext
from ...protocol import OperatorResult
from .common import key_order, manifest, param, read_all_or_empty, refuse, text_column

SPLIT = "_dw_split"
ORIGIN = "_dw_origin"


def specs_of(params: Mapping[str, Any]) -> list[SplitSpec]:
    names = list(param(params, "split_names", ["train", "test"]))
    fractions = list(param(params, "split_fractions", [0.9, 0.1]))
    held = set(param(params, "held_out", ["test"]))
    mode = param(params, "mode", "reassign")
    if mode == "reassign" and len(fractions) != len(names):
        raise refuse(
            "split_fractions_invalid",
            f"{len(names)} split names but {len(fractions)} fractions; give one fraction per split.",
            {"split_names": names, "split_fractions": fractions},
        )
    unknown = sorted(held - set(names))
    if unknown:
        raise refuse(
            "split_fractions_invalid",
            f"Held-out split {unknown[0]!r} is not one of the named splits.",
            {"held_out": sorted(held), "split_names": names},
        )
    if mode != "reassign":
        return [SplitSpec(n, 0.0, n in held) for n in names]
    return [SplitSpec(n, float(f), n in held) for n, f in zip(names, fractions, strict=True)]


class Split:
    manifest = manifest(
        "split",
        "selector",
        "Assigns every row to a named split, stratified by columns you choose, with whole groups "
        "kept together; generated rows never go to a held-out split.",
        scope="dataset",
        params={
            "split_names": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "pattern": "^[A-Za-z0-9_.-]{1,64}$"},
                "minItems": 2,
                "default": ["train", "test"],
                "title": "Splits",
                "x-hint": "The first split takes whatever the others do not.",
            },
            "split_fractions": {
                "type": "array",
                "items": {"type": "number", "minimum": 0, "maximum": 1},
                "default": [0.9, 0.1],
                "title": "Fractions",
                "x-hint": "One per split, adding up to 1.",
            },
            "held_out": {
                "type": "array",
                "items": {"type": "string"},
                "default": ["test"],
                "title": "Held-out splits",
                "x-hint": "Generated rows are never placed in these.",
            },
            "stratify_by": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
                "title": "Stratify by",
                "x-hint": "Each split keeps every stratum's share; default: label and the "
                "balanced column.",
            },
            "group_column": {
                "type": "string",
                "minLength": 1,
                "title": "Group column",
                "x-hint": "Rows sharing a value stay in one split (two edits of one headline).",
                "x-advanced": True,
            },
            "mode": {
                "type": "string",
                "enum": ["reassign", "keep_source"],
                "default": "reassign",
                "title": "Mode",
            },
        },
    )

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        specs = specs_of(params)
        mode = param(params, "mode", "reassign")
        stratify = list(param(params, "stratify_by", []))
        group_column = params.get("group_column")
        try:
            if mode == "reassign":
                check_specs(specs)
        except SplitRefusal as exc:
            raise refuse(exc.code, exc.message, exc.details) from None
        table = read_all_or_empty(ctx, batch)
        if table.num_rows:
            table = key_order(table)
        n = table.num_rows
        current = (
            [None if v is None else str(v) for v in table.column(SPLIT).to_pylist()]
            if SPLIT in table.schema.names
            else [None] * n
        )
        origins = (
            [None if v is None else str(v) for v in table.column(ORIGIN).to_pylist()]
            if ORIGIN in table.schema.names
            else [None] * n
        )
        held = {s.name for s in specs if s.held_out}
        roles = {
            s.name: {
                "held_out": s.held_out,
                "fraction": s.fraction if mode == "reassign" else None,
                "stratify_by": stratify,
                "group_column": group_column,
            }
            for s in specs
        }
        if mode == "keep_source":
            generated = [i for i in range(n) if origins[i] == "generated" and current[i] in held]
            if generated:
                raise refuse(
                    "generated_in_held_out",
                    f"{len(generated)} generated row(s) sit in a held-out split. A held-out split "
                    "holds only rows from sources (FR-002.31).",
                    {"rows": generated[:20]},
                )
            counts: dict[str, int] = {}
            for name in current:
                counts[str(name)] = counts.get(str(name), 0) + 1
            report = {"split_roles": roles, "mode": mode, "counts_by_split": counts}
            return OperatorResult(output=table, report=report)
        strata_columns = [text_column(table, c) for c in stratify]
        strata = [
            "|".join(str(col[i]) if col[i] is not None else NULL_LABEL for col in strata_columns)
            or "all"
            for i in range(n)
        ]
        groups = None
        if group_column:
            groups = [None if v is None else str(v) for v in text_column(table, str(group_column))]
        try:
            plan = plan_split(strata, groups, origins, specs, ctx.step_seed)
        except SplitRefusal as exc:
            raise refuse(exc.code, exc.message, exc.details) from None
        names = plan.names()
        keys = table.column("_dw_row_key").to_pylist()
        occ = table.column("_dw_occurrence").to_pylist()
        events = [
            ctx.assign_split(
                (keys[i], occ[i]),
                names[i],
                "split_assigned",
                f"stratum {strata[i]} drawn to {names[i]}",
            )
            for i in range(n)
            if current[i] != names[i]
        ]
        column = pa.array(names, pa.string())
        if SPLIT in table.schema.names:
            output = table.set_column(table.schema.get_field_index(SPLIT), SPLIT, column)
        else:
            output = table.append_column(SPLIT, column)
        report = {
            "split_roles": roles,
            "mode": mode,
            "counts_by_split_and_stratum": plan.counts,
            "shortfalls": plan.shortfalls,
            "small_strata": plan.small_strata,
            "group_column": group_column,
        }
        return OperatorResult(output=output, events=events, report=report)
