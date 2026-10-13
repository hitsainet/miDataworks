"""``cell_balancer``: cap every (value x label) cell at the smallest (FR-004.37–004.40; T-16).

After it runs, the named column predicts nothing (R-03.20). The prototype is
``scripts/build_balanced.py`` ``format_balanced``. The decision lives in the pure
``services/curation/cells.cell_cap_plan``; this operator loads codes, calls it once, builds events
and re-audits the kept rows on EVERY audited column (FR-004.40), so a new shortcut created by
balancing ("has a body") is visible before the user commits.

Extreme-value flags (FR-004.39) are computed for the balance column AND every other audited
column: Reddit ``news`` is a value of ``source_label``, not of ``format``, and is exactly the second
shortcut US-3 expects the preview to spot. Flags are advice; only the user excludes (a value of the
balance column through ``exclude_values``; a value of another column through a
``metadata_value_filter`` step before this one).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pyarrow as pa

from ....models.enums import ColumnRole
from ....services.curation.audit_service import AuditRefusal, audit_table, audited_and_excluded
from ....services.curation.binning import NULL_LABEL
from ....services.curation.cells import (
    CELL_CAP,
    VALUE_EXCLUDED,
    CellRefusal,
    cell_cap_plan,
    cell_table,
    extreme_values,
)
from ...context import RunContext
from ...protocol import OperatorResult
from .common import key_order, manifest, param, read_all, refuse, text_column
from .shortcut_audit import AUDIT_PARAMS, audit_settings

PARAMS: dict[str, Any] = {
    "column": {
        "type": "string",
        "minLength": 1,
        "title": "Balance column",
        "x-hint": "Every (value x label) cell of this column is capped at the smallest cell.",
    },
    "exclude_values": {
        "type": "array",
        "items": {"type": "string"},
        "default": [],
        "title": "Values to exclude",
        "x-hint": "Rows with these values are dropped first (reason value_excluded).",
    },
    **AUDIT_PARAMS,
}


def _values(table: pa.Table, column: str) -> list[str]:
    return [NULL_LABEL if v is None else v for v in text_column(table, column)]


class CellBalancer:
    manifest = manifest(
        "cell_balancer",
        "selector",
        "Caps every (value x label) cell of one column at the smallest cell, so the column "
        "predicts nothing; flags values that are almost all one label.",
        scope="dataset",
        params=PARAMS,
        required=("column",),
    )

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        column = str(params["column"])
        label_column = str(param(params, "label_column", "label"))
        derived = list(param(params, "exclude_columns", []))
        role = ctx.column_roles.get(column)
        if column == label_column or column in derived or role == ColumnRole.CONTENT:
            why = (
                "the label itself"
                if column == label_column
                else ("label-derived" if column in derived else "a content column")
            )
            raise refuse(
                "invalid_balance_column",
                f"{column!r} is {why}; balance on a metadata column such as a source or a format.",
                {"column": column, "reason": why},
            )
        table = key_order(read_all(ctx))
        if label_column not in table.schema.names:
            raise refuse(
                "no_label_column",
                f"The input has no label column {label_column!r}. Add a labeling step first.",
                {"label_column": label_column},
            )
        labels_raw = text_column(table, label_column)
        if any(v is None for v in labels_raw):
            missing = sum(v is None for v in labels_raw)
            raise refuse(
                "label_has_nulls",
                f"{missing} row(s) have no label. Drop unlabelled rows first (for example with "
                "the labeler's exclusion), then balance.",
                {"rows": missing},
            )
        labels = [str(v) for v in labels_raw]
        values = _values(table, column)
        try:
            plan = cell_cap_plan(
                values,
                labels,
                exclude_values=list(param(params, "exclude_values", [])),
                seed=ctx.step_seed,
            )
        except CellRefusal as exc:
            raise refuse(exc.code, exc.message, exc.details) from None
        flags = [{"column": column, **f} for f in plan.flags]
        audited, _ = audited_and_excluded(
            table.schema.names, ctx.column_roles, label_column, dict.fromkeys(derived, "chosen")
        )
        label_names = sorted(set(labels))
        for other in audited:
            if other == column:
                continue
            other_values = _values(table, other)
            for flag in extreme_values(cell_table(other_values, labels, label_names)):
                flags.append({"column": other, **flag})
        keys = table.column("_dw_row_key").to_pylist()
        occ = table.column("_dw_occurrence").to_pylist()
        events = []
        for i, decision in enumerate(plan.decision.tolist()):
            if decision == VALUE_EXCLUDED:
                events.append(
                    ctx.drop(
                        (keys[i], occ[i]),
                        "value_excluded",
                        f"{column} = {values[i]!r} is excluded",
                        column,
                        text=f"{column}={values[i]}",
                    )
                )
            elif decision == CELL_CAP:
                cell = plan.cells[f"{values[i]}|{labels[i]}"]
                events.append(
                    ctx.drop(
                        (keys[i], occ[i]),
                        "cell_cap",
                        f"cell {values[i]!r} x {labels[i]!r} has {cell['before']} rows, "
                        f"sampled to {plan.cap}",
                        "cell_size",
                        float(str(cell["before"])),
                        plan.cap,
                        "sampled_to",
                        text=f"{values[i]}|{labels[i]}",
                    )
                )
        kept = table.filter(pa.array(plan.kept_mask))
        try:
            reaudit = audit_table(
                kept,
                ctx.column_roles,
                label_column,
                derived=dict.fromkeys(derived, "chosen"),
                seed=ctx.step_seed,
                settings=audit_settings(params),
            )
        except AuditRefusal as exc:
            reaudit = {"refused": {"code": exc.code, "message": exc.message}}
        report = {
            "column": column,
            "label_column": label_column,
            "cap": plan.cap,
            "cells": plan.cells,
            "rows_in": table.num_rows,
            "rows_kept": kept.num_rows,
            "rows_dropped": table.num_rows - kept.num_rows,
            "excluded_values": plan.excluded_values,
            "extreme_values": flags,
            "reaudit": reaudit,
            "sample": ctx.sample,
            # FR-004.41: a split after this step defaults to stratifying by these cells.
            "stratify_default": [label_column, column],
        }
        return OperatorResult(output=kept, events=events, report=report)
