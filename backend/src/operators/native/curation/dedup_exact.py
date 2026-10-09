"""``dedup_exact``: exact deduplication on a comparison key (FR-004.10–004.12, 004.15; T-08).

The comparison key is feature 002's row key (``dw.rowkey/v1``) — rows sharing a row key are exact
duplicates (FR-002.21). With ``collapse_whitespace`` (off by default, T-08) the key is the same
function over inner-whitespace-collapsed content; the rows themselves are never rewritten. The kept
row is the lowest (row key, occurrence); each dropped row's event names it (FR-004.11).

A group whose members sit in more than one split is NOT deduplicated: every member is kept and the
group is reported in ``cross_split_groups``; the leakage check owns that decision (FR-004.15).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pyarrow as pa

from ....services.curation.text_stats import collapse_whitespace
from ....services.row_keys import compute_row_key
from ...context import RunContext
from ...protocol import OperatorResult
from .common import key_order, manifest, param, read_all_or_empty

REPORT_GROUPS = 200


def comparison_keys(table: pa.Table, ctx: RunContext, collapse: bool) -> list[str]:
    if not collapse:
        return [str(k) for k in table.column("_dw_row_key").to_pylist()]
    content = [c for c in ctx.content_columns if c in table.schema.names]
    rows = table.select(content).to_pylist()
    return [
        compute_row_key({c: collapse_whitespace(r[c]) for c in content}, content, ctx.rowkey_scheme)
        for r in rows
    ]


class DedupExact:
    manifest = manifest(
        "dedup_exact",
        "deduplicator",
        "Drops rows whose comparison key repeats an earlier row's, keeping the lowest row key; "
        "duplicates across splits are reported, never dropped.",
        scope="dataset",
        params={
            "collapse_whitespace": {
                "type": "boolean",
                "default": False,
                "title": "Collapse inner whitespace",
                "x-hint": "Treat rows that differ only by spacing as duplicates (the stored rows "
                "are not changed).",
            }
        },
    )

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        table = read_all_or_empty(ctx, batch)
        if table.num_rows == 0:
            return OperatorResult(output=table, report={"groups": 0, "cross_split_groups": []})
        table = key_order(table)
        collapse = bool(param(params, "collapse_whitespace", False))
        ckeys = comparison_keys(table, ctx, collapse)
        keys = table.column("_dw_row_key").to_pylist()
        occ = table.column("_dw_occurrence").to_pylist()
        splits = (
            table.column("_dw_split").to_pylist()
            if "_dw_split" in table.schema.names
            else [None] * table.num_rows
        )
        members: dict[str, list[int]] = {}
        for i, ck in enumerate(ckeys):
            members.setdefault(ck, []).append(i)
        keep = [True] * table.num_rows
        events = []
        cross: list[dict[str, Any]] = []
        groups = 0
        for ck, rows in members.items():
            if len(rows) < 2:
                continue
            groups += 1
            if len({splits[i] for i in rows}) > 1:
                cross.append(
                    {
                        "comparison_key": ck,
                        "rows": len(rows),
                        "splits": sorted({str(splits[i]) for i in rows}),
                    }
                )
                continue
            kept = rows[0]  # rows are in (row key, occurrence) order: the lowest is kept
            for i in rows[1:]:
                keep[i] = False
                events.append(
                    ctx.drop(
                        (keys[i], occ[i]),
                        "exact_duplicate",
                        f"duplicate of {str(keys[kept])[:12]}"
                        + (" (whitespace collapsed)" if collapse else ""),
                        "comparison_key",
                        text=ck,
                        kept_key=str(keys[kept]),
                    )
                )
        report = {
            "comparison": "dw.rowkey/v1" + (" + collapse_whitespace" if collapse else ""),
            "groups": groups,
            "rows_dropped": len(events),
            "cross_split_groups": cross[:REPORT_GROUPS],
            "cross_split_group_count": len(cross),
        }
        return OperatorResult(output=table.filter(pa.array(keep)), events=events, report=report)
