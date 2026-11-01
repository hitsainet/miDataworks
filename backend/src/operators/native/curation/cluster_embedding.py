"""``cluster_embedding``: clusters on an EMBEDDING basis (004 FR-004.24 embedding basis; FTASKS 15.5).

A separate report operator, not a parameter of ``cluster``: an embedding step needs the ``labeling``
queue, the ``embeddings`` endpoint role and the shared miLLM lease (P-05), and a manifest change to
``cluster`` would move every stored lexical clustering's identity (ADR-009). Vectors come from
``services/curation/embedding_service.embed`` (strict, refuse-load, lease header, cache; empty and
over-cap rows are not sent and are reported as unclustered). Unit vectors are clustered with
``MiniBatchKMeans`` seeded by the step seed. The report records ``basis: embedding``, the embedding
specification (never the key), sizes, exemplars and the rows left unclustered.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pyarrow as pa
from sklearn.cluster import MiniBatchKMeans

from ....services.curation import embedding_service
from ....services.curation.audit_service import excerpt
from ...context import RunContext
from ...manifest import OperatorManifest, ResourceSpec
from ...protocol import OperatorResult
from .common import PROVIDER_VERSION, key_order, param, read_all_or_empty, schema
from .dedup_embedding import _texts, embed_table

K = {"type": "integer", "minimum": 2, "maximum": 1000, "default": 50, "title": "Clusters"}

MANIFEST = OperatorManifest(
    name="cluster_embedding",
    version="1.0.0",
    provider="native",
    provider_version=PROVIDER_VERSION,
    kind="report",
    scope="dataset",
    description=(
        "Groups rows into clusters by their embeddings (the embeddings endpoint role) and reports "
        "sizes and exemplars; rows that cannot be embedded are reported, never guessed."
    ),
    params_schema=schema({"k": K}, ()),
    resources=ResourceSpec(
        queue="labeling",
        endpoint_role="embeddings",
        needs_lease=True,
        cpu_class="medium",
        memory_class="medium",
    ),
    deterministic=True,
)


def fit_embeddings(vectors: np.ndarray, k: int, seed: int) -> tuple[np.ndarray, np.ndarray, int]:
    """(labels, distance to own centroid, k) for unit-normalised ``vectors``."""
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    unit = vectors / np.where(norms == 0, 1, norms)
    distinct = len({tuple(np.round(v, 6)) for v in unit})
    k = max(1, min(int(k), distinct))
    model = MiniBatchKMeans(n_clusters=k, random_state=seed, n_init=3, batch_size=1024)
    labels = model.fit_predict(unit).astype(np.int32)
    diff = unit - model.cluster_centers_[labels]
    return labels, np.sqrt((diff * diff).sum(axis=1)), k


class ClusterEmbedding:
    manifest = MANIFEST

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        table = read_all_or_empty(ctx, batch)
        if table.num_rows == 0:
            return OperatorResult(output=table, report={"basis": "embedding", "k": 0})
        table = key_order(table)
        embedded = embed_table(table, ctx)
        ctx.check_cancel()
        index = sorted(embedded.vectors)
        skipped = {
            reason: sum(1 for r in embedded.skipped.values() if r == reason)
            for reason in (embedding_service.OVER_INPUT_CAP, embedding_service.EMPTY_INPUT)
        }
        report: dict[str, Any] = {
            "basis": "embedding",
            "method": "minibatch_kmeans_unit_vectors",
            "embedding": embedded.spec.as_dict(),
            "n_rows": table.num_rows,
            "rows_clustered": len(index),
            "rows_unclustered": skipped,
            "rows_from_cache": embedded.from_cache,
            "requests": embedded.requests,
            "pinned": ctx.lease_id is not None,
            "sample": ctx.sample,
        }
        if not index:
            report.update(k=0, sizes=[], exemplars=[])
            return OperatorResult(output=table, report=report)
        matrix = np.stack([embedded.vectors[i] for i in index]).astype(np.float64)
        labels, distances, k = fit_embeddings(matrix, int(param(params, "k", 50)), ctx.step_seed)
        texts = _texts(table, ctx)
        keys = table.column("_dw_row_key").to_pylist()
        sizes = np.bincount(labels, minlength=k)
        exemplars = []
        for c in range(k):
            members = np.flatnonzero(labels == c)
            if members.size == 0:
                continue
            best = index[int(members[np.argmin(distances[members])])]
            exemplars.append(
                {
                    "cluster": c,
                    "size": int(sizes[c]),
                    "row_key": keys[best],
                    "excerpt": excerpt(texts[best]),
                }
            )
        report.update(k=k, sizes=sorted((int(s) for s in sizes), reverse=True), exemplars=exemplars)
        return OperatorResult(output=table, report=report)
