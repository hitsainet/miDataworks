"""``contamination_check`` (report) and ``decontaminate`` (filter): benchmark overlap
(FR-004.17–004.19).

Both read the benchmark at its PINNED revision through ``contamination_service.load_benchmark``,
which refuses any other revision (``benchmark_revision_unavailable``). That read opens a short
read-only session for the benchmark's source row and files — the one place a 004 operator touches
the database, because a pinned benchmark is a reference the step cannot carry in its parameters
(recorded deviation from 003's "an operator never opens a session"). Each dropped row names the
benchmark, its revision, the item and the overlap statistic (``benchmark_overlap``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pyarrow as pa

from ....core.database import sync_session_factory
from ....services.curation import contamination_service as cs
from ....services.curation.cluster_service import texts_of
from ....services.curation.errors import CurationError
from ...context import RunContext
from ...manifest import ThresholdSpec
from ...protocol import OperatorResult
from .common import manifest, param, read_all_or_empty, refuse

BENCH_PARAMS: dict[str, Any] = {
    "benchmark_source_id": {"type": "string", "minLength": 1, "title": "Benchmark source"},
    "benchmark_revision": {
        "type": "string",
        "minLength": 1,
        "title": "Pinned revision",
        "x-hint": "The benchmark is read at exactly this revision or the step fails.",
    },
    "n": {"type": "integer", "minimum": 3, "maximum": 50, "default": 13, "x-advanced": True},
}


def _bench(params: Mapping[str, Any], n: int) -> cs.Benchmark:
    try:
        with sync_session_factory()() as session:
            return cs.load_benchmark(
                session, str(params["benchmark_source_id"]), n, str(params["benchmark_revision"])
            )
    except CurationError as exc:
        raise refuse(exc.code, exc.message, exc.details) from None


class ContaminationCheck:
    manifest = manifest(
        "contamination_check",
        "report",
        "Measures each row's word n-gram overlap with a pinned benchmark.",
        scope="dataset",
        params=BENCH_PARAMS,
        required=("benchmark_source_id", "benchmark_revision"),
    )

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        table = read_all_or_empty(ctx, batch)
        n = int(param(params, "n", cs.DEFAULT_N))
        bench = _bench(params, n)
        stats = cs.overlaps(texts_of(table, ctx.content_columns), bench, n)
        hits = [s for s, _ in stats if s > 0]
        report = {
            "benchmark": bench.name,
            "revision": bench.revision,
            "n": n,
            "rows_overlapping": len(hits),
            "max_overlap": max(hits, default=0.0),
            "n_rows": table.num_rows,
            "sample": ctx.sample,
        }
        return OperatorResult(output=table, report=report)


class Decontaminate:
    manifest = manifest(
        "decontaminate",
        "filter",
        "Drops rows whose word n-grams overlap a pinned benchmark at or above a threshold, naming "
        "the benchmark, revision and item.",
        scope="dataset",
        params={
            **BENCH_PARAMS,
            "threshold": {
                "type": "number",
                "exclusiveMinimum": 0,
                "maximum": 1,
                "default": 0.5,
                "x-unit": "share of n-grams",
                "x-widget": "slider",
            },
        },
        required=("benchmark_source_id", "benchmark_revision"),
        thresholds=(
            ThresholdSpec(
                param="threshold",
                statistic="ngram_overlap",
                unit="share of n-grams",
                drop_when="above",
            ),
        ),
    )

    def compute_statistics(
        self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext
    ) -> dict[str, pa.Array]:
        table = batch if ctx.input_reader is None else read_all_or_empty(ctx, batch)
        n = int(param(params, "n", cs.DEFAULT_N))
        stats = cs.overlaps(texts_of(table, ctx.content_columns), _bench(params, n), n)
        return {"ngram_overlap": pa.array([s for s, _ in stats], pa.float64())}

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        table = read_all_or_empty(ctx, batch)
        if table.num_rows == 0:
            return OperatorResult(output=table)
        n = int(param(params, "n", cs.DEFAULT_N))
        threshold = float(param(params, "threshold", 0.5))
        bench = _bench(params, n)
        stats = cs.overlaps(texts_of(table, ctx.content_columns), bench, n)
        keys = table.column("_dw_row_key").to_pylist()
        occ = table.column("_dw_occurrence").to_pylist()
        keep, events = [], []
        for i, (share, item) in enumerate(stats):
            if share >= threshold:
                label = bench.label(item) if item is not None else f"{bench.name}@{bench.revision}"
                events.append(
                    ctx.drop(
                        (keys[i], occ[i]),
                        "benchmark_overlap",
                        f"{share:.0%} of its {n}-grams appear in {label}",
                        "ngram_overlap",
                        share,
                        threshold,
                        ">=",
                        text=label,
                    )
                )
            else:
                keep.append(i)
        return OperatorResult(
            output=table.take(pa.array(keep, pa.int64())),
            events=events,
            report={"benchmark": bench.name, "revision": bench.revision},
        )
