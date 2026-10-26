"""``screen_filter@1``: drop real-text rows a screening rule signal fired on (017 FR-017.32,
FR-017.33; FTID §7.6, §16 X-35).

016's ``rule_set@1`` with ``use = screen`` WRITES flag columns and drops nothing; this **filter**
drops. ``flag_columns`` lists the boolean outputs of every screening rule set the recipe declares
(each ``rule_set@1`` step's prefixed outputs: the behaviour's own positive signals and any other
rule set run as a screen). A row is dropped when ANY of them is true (``screened_out``, the
statistic is the column that fired) or when one is null - an unscreened row is never assumed clean
(``not_screened``). Every kept row was screened by every listed column.

**Privacy (FR-017.32).** The drop log carries the row key, the SOURCE (``statistic_text =
source=<source id>``, read from the row's ``_dw_source_id``), the signal (the statistic's column)
and the reason; never the matched text or the row's content. A listed column the input does not
have is refused ``screen_column_missing`` (recipe validation names it ``missing_input_column``:
no step before writes it). No behaviour is named here.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

import pyarrow as pa

from ...context import RunContext
from ...errors import StepFailed
from ...protocol import OperatorResult
from ..curation.common import manifest
from .common import NEXT_VALIDATE

NAME = "screen_filter"
SOURCE = "_dw_source_id"


class ScreenFilter:
    manifest = manifest(
        NAME,
        "filter",
        "Drops real-text rows on which any screening rule signal fired (the boolean outputs of "
        "every rule_set@1 step run with use = screen), and rows a screen did not reach. The drop "
        "log names the row, its source, the signal and the reason - never the text.",
        params={
            "flag_columns": {
                "type": "array",
                "items": {"type": "string", "pattern": "^[A-Za-z_][A-Za-z0-9_]{0,127}$"},
                "minItems": 1,
                "maxItems": 256,
                "uniqueItems": True,
                "title": "Screening flag columns",
                "x-hint": "Each screening rule_set@1 step's boolean outputs, with its prefix "
                "(for example scr_<output>); a row any of them flags is dropped.",
            },
        },
        required=("flag_columns",),
    )

    def columns_for(self, params: Mapping[str, Any]) -> tuple[list[str], list[str], list[str]]:
        return [str(c) for c in params.get("flag_columns") or []], [], []

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        columns = [str(c) for c in params["flag_columns"]]
        missing = [c for c in columns if c not in batch.schema.names]
        if missing:
            raise StepFailed(
                "screen_column_missing",
                f"screen_filter@1 reads {missing}, which no step before it writes: run each "
                "screening rule set (rule_set@1, use = screen) before the filter, with the prefix "
                "the column names.",
                {"columns": missing, "next": NEXT_VALIDATE},
            )
        for column in columns:
            if not pa.types.is_boolean(batch.schema.field(column).type):
                raise StepFailed(
                    "screen_column_not_boolean",
                    f"{column!r} holds {batch.schema.field(column).type}, not true or false: "
                    "name a fired output of the screening rule set.",
                    {"column": column, "next": NEXT_VALIDATE},
                )
        flags = {c: batch.column(c).to_pylist() for c in columns}
        keys = batch.column("_dw_row_key").to_pylist()
        occurrences = batch.column("_dw_occurrence").to_pylist()
        sources = (
            batch.column(SOURCE).to_pylist() if SOURCE in batch.schema.names else [None] * len(keys)
        )
        keep: list[int] = []
        events = []
        fired: Counter[str] = Counter()
        by_source: Counter[str] = Counter()
        unscreened = 0
        for i in range(batch.num_rows):
            source = f"source={sources[i]}"
            hit = next((c for c in columns if flags[c][i] is True), None)
            blank = next((c for c in columns if flags[c][i] is None), None)
            if hit is not None:
                fired[hit] += 1
                by_source[str(sources[i])] += 1
                events.append(
                    ctx.drop(
                        (str(keys[i]), int(occurrences[i])),
                        "screened_out",
                        f"screening signal {hit} fired on this row",
                        hit,
                        text=source,
                    )
                )
            elif blank is not None:
                unscreened += 1
                by_source[str(sources[i])] += 1
                events.append(
                    ctx.drop(
                        (str(keys[i]), int(occurrences[i])),
                        "not_screened",
                        f"screening signal {blank} has no value on this row: never assumed clean",
                        blank,
                        text=source,
                    )
                )
            else:
                keep.append(i)
        output = batch.take(pa.array(keep, pa.int64())) if len(keep) != batch.num_rows else batch
        return OperatorResult(
            output=output,
            events=events,
            report={
                "screen": {
                    "columns": columns,
                    "screened_out": dict(sorted(fired.items())),
                    "not_screened": unscreened,
                    "dropped_by_source": dict(sorted(by_source.items())),
                }
            },
        )
