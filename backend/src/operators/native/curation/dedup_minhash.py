"""``dedup_minhash``: near deduplication with MinHash and LSH (FR-004.11, 004.13, 004.15; P-18).

Signatures stream from ``ctx.input_reader``; candidates come from shared LSH bands, are verified on
the full signatures, joined by union-find, and each group keeps its lowest (row key, occurrence).
Every dropped row names the kept row and the estimated Jaccard similarity against the threshold.
A group spanning splits is kept whole and reported (FR-004.15). The result records its basis
(``lexical``) and band layout. ``compute_statistics`` gives each row's best estimated similarity to
any candidate, for 003's threshold control.

Deviation (FTID §7.5): band grouping runs in numpy over the in-memory band-key array rather than as
a DuckDB ``GROUP BY`` over a signatures Parquet; memory is ``rows x bands x 8`` bytes (128 MB at a
million rows and 16 bands). Measured in acceptance (FTASKS 17.3).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pyarrow as pa

from ....services.curation import minhash
from ....services.curation.text_stats import as_text
from ...context import RunContext
from ...manifest import ThresholdSpec
from ...protocol import OperatorResult
from .common import key_order, manifest, param, read_all_or_empty

PARAMS: dict[str, Any] = {
    "threshold": {
        "type": "number",
        "exclusiveMinimum": 0,
        "maximum": 1,
        "default": 0.8,
        "title": "Similarity threshold",
        "x-unit": "estimated Jaccard",
        "x-widget": "slider",
    },
    "shingle": {"type": "string", "enum": ["word", "char"], "default": "word"},
    "size": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
    "permutations": {
        "type": "integer",
        "minimum": 16,
        "maximum": 1024,
        "default": 128,
        "x-advanced": True,
    },
}


def _texts(table: pa.Table, ctx: RunContext) -> list[str]:
    content = [c for c in ctx.content_columns if c in table.schema.names]
    columns = [table.column(c).to_pylist() for c in content]
    if not columns:
        return [""] * table.num_rows
    return ["\n".join(as_text(v) for v in vals) for vals in zip(*columns, strict=True)]


def _signatures(table: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> np.ndarray:
    return minhash.signatures_for(
        _texts(table, ctx),
        kind=str(param(params, "shingle", "word")),
        size=int(param(params, "size", 5)),
        count=int(param(params, "permutations", 128)),
        seed=ctx.step_seed,
    )


class DedupMinhash:
    manifest = manifest(
        "dedup_minhash",
        "deduplicator",
        "Drops near-duplicate rows (MinHash, estimated Jaccard at or above a threshold), keeping "
        "the lowest row key; groups across splits are reported, never dropped.",
        scope="dataset",
        params=PARAMS,
        thresholds=(
            ThresholdSpec(
                param="threshold",
                statistic="jaccard_estimate",
                unit="estimated Jaccard",
                drop_when="above",
            ),
        ),
    )

    def compute_statistics(
        self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext
    ) -> dict[str, pa.Array]:
        table = batch if ctx.input_reader is None else read_all_or_empty(ctx, batch)
        sigs = _signatures(table, params, ctx)
        layout = minhash.band_layout(int(param(params, "permutations", 128)), 0.5)
        best = np.zeros(table.num_rows)
        for i, j in minhash.candidate_pairs(minhash.band_keys(sigs, layout)):
            e = minhash.jaccard_estimate(sigs[i], sigs[j])
            best[i], best[j] = max(best[i], e), max(best[j], e)
        return {"jaccard_estimate": pa.array(best.tolist(), pa.float64())}

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        table = read_all_or_empty(ctx, batch)
        threshold = float(param(params, "threshold", 0.8))
        count = int(param(params, "permutations", 128))
        layout = minhash.band_layout(count, threshold)
        base = {
            "basis": "lexical",
            "threshold": threshold,
            "band_layout": {"bands": layout.bands, "rows": layout.rows},
        }
        if table.num_rows == 0:
            return OperatorResult(output=table, report={**base, "groups": 0})
        table = key_order(table)
        sigs = _signatures(table, params, ctx)
        ctx.check_cancel()
        groups, verified = minhash.near_duplicate_groups(sigs, layout, threshold)
        keys = table.column("_dw_row_key").to_pylist()
        occ = table.column("_dw_occurrence").to_pylist()
        splits = (
            table.column("_dw_split").to_pylist()
            if "_dw_split" in table.schema.names
            else [None] * table.num_rows
        )
        keep = [True] * table.num_rows
        events, cross = [], []
        for members in groups.values():
            if len({splits[i] for i in members}) > 1:
                cross.append(
                    {
                        "rows": len(members),
                        "splits": sorted({str(splits[i]) for i in members}),
                        "row_keys": [str(keys[i]) for i in members[:10]],
                    }
                )
                continue
            kept = members[0]  # lowest (row key, occurrence): rows are in key order
            for i in members[1:]:
                keep[i] = False
                estimate = minhash.jaccard_estimate(sigs[kept], sigs[i])
                events.append(
                    ctx.drop(
                        (keys[i], occ[i]),
                        "near_duplicate_minhash",
                        f"near duplicate of {str(keys[kept])[:12]} (estimated Jaccard "
                        f"{estimate:.2f})",
                        "jaccard_estimate",
                        estimate,
                        threshold,
                        ">=",
                        kept_key=str(keys[kept]),
                    )
                )
        report = {
            **base,
            "groups": len(groups),
            "verified_pairs": len(verified),
            "rows_dropped": len(events),
            "cross_split_groups": cross[:200],
            "cross_split_group_count": len(cross),
        }
        return OperatorResult(output=table.filter(pa.array(keep)), events=events, report=report)
