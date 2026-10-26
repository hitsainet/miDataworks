"""``label_balancer``: equal classes by seeded undersampling, per split (FR-004.52–004.57).

Separate from ``cell_balancer`` (which refuses the label column by design, FR-004.38): see
``services/curation/label_balance.py`` and ``docs/curation.md``. Every report carries the leakage
warning; there is no parameter that removes it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pyarrow as pa

from ....services.curation.audit_service import AuditRefusal, audit_table
from ....services.curation.label_balance import (
    LEAKAGE_WARNING,
    LabelBalanceRefusal,
    label_balance_plan,
)
from ...context import RunContext
from ...protocol import OperatorResult
from .common import key_order, manifest, param, read_all, refuse, text_column
from .shortcut_audit import AUDIT_PARAMS, audit_settings

PARAMS: dict[str, Any] = {
    "classes": {
        "type": "array",
        "items": {"type": "string"},
        "default": [],
        "title": "Expected classes",
        "x-hint": "Optional. A listed class with no rows in a split is refused, never capped at zero.",
    },
    "max_per_class": {
        "type": "integer",
        "minimum": 1,
        "title": "Rows per class (cap)",
        "x-hint": "Optional. Every class is sampled down to the smallest class, or to this cap.",
    },
    **AUDIT_PARAMS,
}


class LabelBalancer:
    manifest = manifest(
        "label_balancer",
        "selector",
        "Samples every class of the label down to the smallest, within each split. WARNING: rows "
        "are selected by their label, so the class balance is artificial; the version, its "
        "lineage and its publish record say so.",
        scope="dataset",
        params=PARAMS,
    )

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        label_column = str(param(params, "label_column", "label"))
        derived = list(param(params, "exclude_columns", []))
        table = key_order(read_all(ctx))
        if label_column not in table.schema.names:
            raise refuse(
                "no_label_column",
                f"The input has no label column {label_column!r}. Add a labeling step first.",
                {"label_column": label_column},
            )
        labels_raw = text_column(table, label_column)
        missing = sum(v is None for v in labels_raw)
        if missing:
            raise refuse(
                "label_has_nulls",
                f"{missing} row(s) have no label. Drop unlabelled rows first, then balance.",
                {"rows": missing},
            )
        labels = [str(v) for v in labels_raw]
        splits = (
            [s if s is not None else "" for s in table.column("_dw_split").to_pylist()]
            if "_dw_split" in table.schema.names
            else [""] * table.num_rows
        )
        try:
            plan = label_balance_plan(
                labels,
                splits,
                seed=ctx.step_seed,
                classes=list(param(params, "classes", [])) or None,
                cap=params.get("max_per_class"),
            )
        except LabelBalanceRefusal as exc:
            raise refuse(exc.code, exc.message, exc.details) from None
        keys = table.column("_dw_row_key").to_pylist()
        occ = table.column("_dw_occurrence").to_pylist()
        events = []
        for i, kept in enumerate(plan.kept.tolist()):
            if kept:
                continue
            split = splits[i]
            before = plan.classes[split][labels[i]]["before"]
            events.append(
                ctx.drop(
                    (keys[i], occ[i]),
                    "label_cap",
                    f"class {labels[i]!r} has {before} rows in split {split!r}, sampled to "
                    f"{plan.caps[split]}",
                    "class_size",
                    float(before),
                    plan.caps[split],
                    "sampled_to",
                    text=f"{split}|{labels[i]}",
                )
            )
        kept_table = table.filter(pa.array(plan.kept))
        try:
            reaudit: dict[str, Any] = audit_table(
                kept_table,
                ctx.column_roles,
                label_column,
                derived=dict.fromkeys(derived, "chosen"),
                seed=ctx.step_seed,
                settings=audit_settings(params),
            )
        except AuditRefusal as exc:
            reaudit = {"refused": {"code": exc.code, "message": exc.message}}
        report = {
            "label_column": label_column,
            "caps": plan.caps,
            "classes": plan.classes,
            "rows_in": table.num_rows,
            "rows_kept": kept_table.num_rows,
            "rows_dropped": table.num_rows - kept_table.num_rows,
            "reaudit": reaudit,
            "leakage_warning": dict(LEAKAGE_WARNING),
            "sample": ctx.sample,
        }
        return OperatorResult(output=kept_table, events=events, report=report)
