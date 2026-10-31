"""``shortcut_audit``: the audit as a report step (FR-004.26–004.32; FTDD 004 §6.2).

Thin: it calls the same ``audit_service.audit_table`` the post-build audit and the API use (one
implementation, two entry points, FTID §3.2). A report touches no row. An operator never opens a
database session, so label-derived columns are a parameter the caller resolves from the draft's
labeler steps (``exclude_columns``), as the split operator's ``stratify_by`` default is.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pyarrow as pa

from ....services.curation.audit_service import AuditRefusal, AuditSettings, audit_table
from ...context import RunContext
from ...protocol import OperatorResult
from .common import manifest, param, read_all_or_empty, refuse

AUDIT_PARAMS: dict[str, Any] = {
    "label_column": {
        "type": "string",
        "minLength": 1,
        "default": "label",
        "title": "Label column",
        "x-hint": "The categorical label every metadata column is tested against.",
    },
    "exclude_columns": {
        "type": "array",
        "items": {"type": "string"},
        "default": [],
        "title": "Label-derived columns",
        "x-hint": "Columns a labeler wrote for this label (a probability); never scored.",
        "x-advanced": True,
    },
    "folds": {"type": "integer", "minimum": 2, "maximum": 20, "default": 5, "x-advanced": True},
    "control_runs": {
        "type": "integer",
        "minimum": 1,
        "maximum": 50,
        "default": 5,
        "x-advanced": True,
    },
    "tolerance_pp": {
        "type": "number",
        "minimum": 0,
        "maximum": 49,
        "default": 2,
        "x-unit": "points",
        "x-advanced": True,
    },
}


def audit_settings(params: Mapping[str, Any]) -> AuditSettings:
    return AuditSettings(
        folds=int(param(params, "folds", 5)),
        control_runs=int(param(params, "control_runs", 5)),
        tolerance_pp=float(param(params, "tolerance_pp", 2)),
    )


class ShortcutAudit:
    manifest = manifest(
        "shortcut_audit",
        "report",
        "Tests every metadata column for predicting the label on held-out folds, against chance "
        "and a permuted-label control.",
        scope="dataset",
        params=AUDIT_PARAMS,
    )

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        table = read_all_or_empty(ctx, batch)
        derived = dict.fromkeys(param(params, "exclude_columns", []), "chosen")
        try:
            report = audit_table(
                table,
                ctx.column_roles,
                str(param(params, "label_column", "label")),
                derived=derived,
                seed=ctx.step_seed,
                settings=audit_settings(params),
                label_source="chosen",
            )
        except AuditRefusal as exc:
            raise refuse(exc.code, exc.message, exc.details) from None
        report["sample"] = ctx.sample
        return OperatorResult(output=table, report=report)
