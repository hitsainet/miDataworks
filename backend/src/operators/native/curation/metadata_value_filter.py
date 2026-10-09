"""``metadata_value_filter``: drop rows whose named column holds listed values (FR-004.16).

The prototype's exclusion of Reddit ``news`` (``scripts/build_balanced.py``). ``(empty)`` in the
list matches a null value, as the audit names it. Dropped rows carry ``value_excluded`` naming the
column and value.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pyarrow as pa

from ....services.curation.binning import NULL_LABEL
from ...context import RunContext
from ...protocol import OperatorResult
from .common import manifest, text_column


class MetadataValueFilter:
    manifest = manifest(
        "metadata_value_filter",
        "filter",
        "Drops rows whose named column holds one of the listed values.",
        params={
            "column": {"type": "string", "minLength": 1, "title": "Column"},
            "values": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "title": "Values to drop",
                "x-hint": "Use (empty) for rows where the column is empty.",
            },
        },
        required=("column", "values"),
    )

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        if batch.num_rows == 0:
            return OperatorResult(output=batch)
        column = str(params["column"])
        drop = set(params["values"])
        values = [NULL_LABEL if v is None else v for v in text_column(batch, column)]
        keys = batch.column("_dw_row_key").to_pylist()
        occ = batch.column("_dw_occurrence").to_pylist()
        keep, events = [], []
        for i, value in enumerate(values):
            if value in drop:
                events.append(
                    ctx.drop(
                        (keys[i], occ[i]),
                        "value_excluded",
                        f"{column} = {value!r} is excluded",
                        column,
                        text=f"{column}={value}",
                    )
                )
            else:
                keep.append(i)
        return OperatorResult(output=batch.take(pa.array(keep, pa.int64())), events=events)
