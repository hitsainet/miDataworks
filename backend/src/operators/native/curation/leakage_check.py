"""``leakage_check``: exact, near and group leakage across a step's splits, as a report (FR-004.20).

The same ``leakage_service.check_table`` the API, the post-split run (FR-004.50) and features 008
and 009 use; here the sides are the input's ``_dw_split`` values. A report touches no row.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pyarrow as pa

from ....services.curation import leakage_service
from ...context import RunContext
from ...protocol import OperatorResult
from .common import manifest, param, read_all_or_empty, refuse

PARAMS: dict[str, Any] = {
    "group_column": {
        "type": "string",
        "minLength": 1,
        "title": "Group column",
        "x-hint": "Rows sharing a value must not sit in different splits.",
    },
    "threshold": {
        "type": "number",
        "exclusiveMinimum": 0,
        "maximum": 1,
        "default": 0.8,
        "title": "Near-duplicate threshold",
        "x-unit": "estimated Jaccard",
    },
    "permutations": {
        "type": "integer",
        "minimum": 16,
        "maximum": 1024,
        "default": 128,
        "x-advanced": True,
    },
}


class LeakageCheck:
    manifest = manifest(
        "leakage_check",
        "report",
        "Finds exact duplicates, near duplicates (MinHash) and shared groups across splits.",
        scope="dataset",
        params=PARAMS,
        thresholds=(),
    )

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        table = read_all_or_empty(ctx, batch)
        if "_dw_split" not in table.schema.names:
            raise refuse("no_splits", "The input has no splits to compare; add a split step.")
        sides = table.column("_dw_split").cast(pa.string())
        work = table.append_column(leakage_service.SIDE, sides)
        cfg = {
            "group_column": params.get("group_column"),
            "threshold": float(param(params, "threshold", 0.8)),
            "permutations": int(param(params, "permutations", 128)),
            "shingle": leakage_service.DEFAULT_SHINGLE,
            "size": leakage_service.DEFAULT_SIZE,
        }
        result, _ = leakage_service.check_table(work, ctx.content_columns, cfg, ctx.step_seed)
        result["sample"] = ctx.sample
        return OperatorResult(output=table, report=result)
