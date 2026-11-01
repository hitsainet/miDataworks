"""``cluster`` (report) and ``cluster_balancer`` (selector): lexical clusters and per-cluster caps
(FR-004.24, 004.25; P-18). The basis (``lexical``), method and vectoriser are recorded with every
result; the cap is an absolute count or a share of the input; within a cluster rows are sampled with
the step seed, and each dropped row carries ``cluster_cap`` naming the cluster, its size and the cap.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pyarrow as pa

from ....services.curation import cluster_service
from ...context import RunContext
from ...protocol import OperatorResult
from .common import key_order, manifest, param, read_all_or_empty, refuse

K = {"type": "integer", "minimum": 2, "maximum": 1000, "default": 50, "title": "Clusters"}


class Cluster:
    manifest = manifest(
        "cluster",
        "report",
        "Groups rows into clusters on a lexical basis and reports sizes and exemplars.",
        scope="dataset",
        params={"k": K},
    )

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        table = read_all_or_empty(ctx, batch)
        if table.num_rows == 0:
            return OperatorResult(output=table, report={"basis": "lexical", "k": 0})
        texts = cluster_service.texts_of(table, ctx.content_columns)
        fitted = cluster_service.fit(texts, int(param(params, "k", 50)), ctx.step_seed)
        report = cluster_service.summary(table, texts, fitted)
        report["sample"] = ctx.sample
        return OperatorResult(output=table, report=report)


class ClusterBalancer:
    manifest = manifest(
        "cluster_balancer",
        "selector",
        "Caps each cluster at a number of rows or a share of the input, sampling with the seed.",
        scope="dataset",
        # 1.1.0 (feature 007, T-33): the additive ``applies_to`` parameter. A manifest change is a
        # version change (ADR-009); recipes on 1.0.0 move with "Clone recipe with current
        # operators" (T-11).
        version="1.1.0",
        params={
            "k": K,
            "cap": {"type": "integer", "minimum": 1, "title": "Cap (rows per cluster)"},
            "cap_share": {
                "type": "number",
                "exclusiveMinimum": 0,
                "maximum": 1,
                "title": "Cap (share of the input per cluster)",
            },
            # Feature 007 (T-33): cap generated rows only, by default at the largest cluster of
            # the source rows (the reference); source rows are never dropped. Additive: the
            # default "all" is this operator's behaviour before 007.
            "applies_to": {
                "type": "string",
                "enum": ["all", "generated"],
                "default": "all",
                "title": "Rows the cap applies to",
            },
        },
    )

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        generated_only = param(params, "applies_to", "all") == "generated"
        if params.get("cap") is None and params.get("cap_share") is None and not generated_only:
            raise refuse("params_invalid", "Give a cap: rows per cluster or a share of the input.")
        table = read_all_or_empty(ctx, batch)
        if table.num_rows == 0:
            return OperatorResult(output=table)
        table = key_order(table)
        texts = cluster_service.texts_of(table, ctx.content_columns)
        fitted = cluster_service.fit(texts, int(param(params, "k", 50)), ctx.step_seed)
        origins = (
            table.column("_dw_origin").to_pylist()
            if "_dw_origin" in table.schema.names
            else [None] * table.num_rows
        )
        candidate = np.array(
            [(o == "generated") if generated_only else True for o in origins], dtype=bool
        )
        if params.get("cap") is not None:
            cap = int(params["cap"])
        elif params.get("cap_share") is not None:
            cap = max(1, round(float(params["cap_share"]) * table.num_rows))
        else:  # T-33: the reference's (source rows') largest cluster
            source_labels = fitted.labels[~candidate]
            cap = int(np.bincount(source_labels).max()) if source_labels.size else 1
        rng = np.random.default_rng(ctx.step_seed)
        keys = table.column("_dw_row_key").to_pylist()
        occ = table.column("_dw_occurrence").to_pylist()
        keep = np.ones(table.num_rows, dtype=bool)
        events = []
        sizes = np.bincount(fitted.labels[candidate], minlength=fitted.k)
        for c in range(fitted.k):
            members = np.flatnonzero((fitted.labels == c) & candidate)
            if members.size <= cap:
                continue
            for i in members[rng.permutation(members.size)][cap:]:
                keep[i] = False
                events.append(
                    ctx.drop(
                        (keys[i], occ[i]),
                        "cluster_cap",
                        f"cluster {c} has {int(sizes[c])} rows, sampled to {cap}",
                        "cluster_size",
                        float(sizes[c]),
                        cap,
                        "sampled_to",
                        text=str(c),
                    )
                )
        report = {
            "basis": "lexical",
            "k": fitted.k,
            "cap": cap,
            "applies_to": "generated" if generated_only else "all",
            "sizes": [int(s) for s in sizes],
            "rows_kept": int(keep.sum()),
        }
        return OperatorResult(output=table.filter(pa.array(keep)), events=events, report=report)
