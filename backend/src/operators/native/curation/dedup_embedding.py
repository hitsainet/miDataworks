"""``dedup_embedding``: near deduplication by embedding cosine (004 FR-004.14; FTASKS 15.2, 15.3).

Runs on the ``labeling`` queue with the ``embeddings`` endpoint role and the shared miLLM lease
(P-05): vectors come from ``services/curation/embedding_service.embed`` (strict and refuse-load
headers, the lease header, the cache). Similarity is **blocked exact cosine** over unit vectors,
``CURATION_EMBED_EXACT_MAX_ROWS`` at most; a larger input is refused (``embed_rows_over_cap``),
never sampled. Pairs at or above the threshold are joined (union-find); each group keeps its lowest
(row key, occurrence) and every other member is dropped naming the kept row and its cosine. A group
spanning splits is kept whole and reported (as ``dedup_minhash`` does, FR-004.15). A row over the
input cap is not embedded, compared with nothing, kept, and counted in the report. The report
records ``basis: embedding`` and the embedding specification (no key).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pyarrow as pa

from ....clients.endpoint_caller import EndpointCaller
from ....core.config import get_settings
from ....services.curation import embedding_service
from ....services.curation.text_stats import as_text
from ...context import RunContext
from ...errors import OperatorError
from ...manifest import OperatorManifest, ResourceSpec, ThresholdSpec
from ...protocol import OperatorResult
from .common import PROVIDER_VERSION, key_order, param, read_all_or_empty, schema

BLOCK = 2048
PARAMS: dict[str, Any] = {
    "threshold": {
        "type": "number",
        "exclusiveMinimum": 0,
        "maximum": 1,
        "default": 0.95,
        "title": "Similarity threshold",
        "x-unit": "cosine",
        "x-widget": "slider",
    },
}

MANIFEST = OperatorManifest(
    name="dedup_embedding",
    version="1.0.0",
    provider="native",
    provider_version=PROVIDER_VERSION,
    kind="deduplicator",
    scope="dataset",
    description=(
        "Drops near-duplicate rows by embedding cosine at or above a threshold, keeping the lowest "
        "row key; groups across splits are reported, never dropped. Rows over the input cap are "
        "kept and not compared."
    ),
    params_schema=schema(PARAMS, ()),
    thresholds=(
        ThresholdSpec(param="threshold", statistic="cosine", unit="cosine", drop_when="above"),
    ),
    resources=ResourceSpec(
        queue="labeling",
        endpoint_role="embeddings",
        needs_lease=True,
        cpu_class="medium",
        memory_class="medium",
    ),
    deterministic=True,
)


def _texts(table: pa.Table, ctx: RunContext) -> list[str]:
    content = [c for c in ctx.content_columns if c in table.schema.names]
    columns = [table.column(c).to_pylist() for c in content]
    if not columns:
        return [""] * table.num_rows
    return ["\n".join(as_text(v) for v in vals) for vals in zip(*columns, strict=True)]


def embed_table(table: pa.Table, ctx: RunContext) -> embedding_service.Embedded:
    cap = int(get_settings().curation_embed_exact_max_rows)
    if table.num_rows > cap:
        raise OperatorError(
            "embed_rows_over_cap",
            f"Embedding dedup compares every pair exactly and takes at most {cap:,} rows; this "
            f"input has {table.num_rows:,}. Split the version, or use dedup_minhash.",
            {"rows": table.num_rows, "cap": cap},
        )
    endpoint = ctx.endpoint
    spec = embedding_service.spec_for(endpoint.base_url, endpoint.model)
    with EndpointCaller(
        endpoint.base_url, endpoint.api_key, key_origin=endpoint.key_origin
    ) as caller:
        return embedding_service.embed(
            caller,
            spec,
            [str(k) for k in table.column("_dw_row_key").to_pylist()],
            _texts(table, ctx),
            lease_id=ctx.lease_id,
            check_cancel=ctx.check_cancel,
        )


def similar_pairs(
    vectors: dict[int, np.ndarray], threshold: float
) -> tuple[list[tuple[int, int, float]], np.ndarray]:
    """Pairs (i < j) with cosine >= threshold, and each row's best cosine to any other row."""
    index = sorted(vectors)
    best = np.zeros(max(index) + 1 if index else 0)
    if len(index) < 2:
        return [], best
    matrix = np.stack([vectors[i] for i in index]).astype(np.float64)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix = matrix / np.where(norms == 0, 1, norms)
    ids = np.asarray(index)
    pairs: list[tuple[int, int, float]] = []
    for start in range(0, len(index), BLOCK):
        block = matrix[start : start + BLOCK]
        sims = block @ matrix.T
        for r in range(sims.shape[0]):
            row = start + r
            sims[r, row] = -1.0  # never a row with itself
            best[ids[row]] = max(best[ids[row]], float(sims[r].max()))
            for col in np.nonzero(sims[r] >= threshold)[0]:
                if col > row:
                    pairs.append((int(ids[row]), int(ids[col]), float(sims[r, col])))
    return pairs, best


def _groups(n: int, pairs: list[tuple[int, int, float]]) -> dict[int, list[int]]:
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, j, _ in pairs:
        a, b = find(i), find(j)
        if a != b:
            parent[max(a, b)] = min(a, b)
    groups: dict[int, list[int]] = {}
    for i in {x for p in pairs for x in p[:2]}:
        groups.setdefault(find(i), []).append(i)
    return {k: sorted(v) for k, v in groups.items()}


class DedupEmbedding:
    manifest = MANIFEST

    def compute_statistics(
        self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext
    ) -> dict[str, pa.Array]:
        table = batch if ctx.input_reader is None else read_all_or_empty(ctx, batch)
        embedded = embed_table(table, ctx)
        _, best = similar_pairs(embedded.vectors, 1.0)
        values = [
            float(best[i]) if i in embedded.vectors and i < len(best) else None
            for i in range(table.num_rows)
        ]
        return {"cosine": pa.array(values, pa.float64())}

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        table = read_all_or_empty(ctx, batch)
        threshold = float(param(params, "threshold", 0.95))
        if table.num_rows == 0:
            return OperatorResult(
                output=table, report={"basis": "embedding", "threshold": threshold, "groups": 0}
            )
        table = key_order(table)
        embedded = embed_table(table, ctx)
        ctx.check_cancel()
        pairs, _ = similar_pairs(embedded.vectors, threshold)
        groups = _groups(table.num_rows, pairs)
        cosine = {(i, j): c for i, j, c in pairs}
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
            kept = members[0]
            for i in members[1:]:
                keep[i] = False
                value = cosine.get((kept, i))
                if value is None:  # joined through another member: measure against the kept row
                    a, b = embedded.vectors[kept], embedded.vectors[i]
                    value = float(np.dot(a, b) / ((np.linalg.norm(a) * np.linalg.norm(b)) or 1))
                events.append(
                    ctx.drop(
                        (keys[i], occ[i]),
                        "near_duplicate_embedding",
                        f"near duplicate of {str(keys[kept])[:12]} (cosine {value:.3f})",
                        "cosine",
                        value,
                        threshold,
                        ">=",
                        kept_key=str(keys[kept]),
                    )
                )
        report = {
            "basis": "embedding",
            "threshold": threshold,
            "embedding": embedded.spec.as_dict(),
            "rows_embedded": len(embedded.vectors),
            "rows_from_cache": embedded.from_cache,
            "requests": embedded.requests,
            "rows_skipped": {
                reason: sum(1 for r in embedded.skipped.values() if r == reason)
                for reason in (embedding_service.OVER_INPUT_CAP, embedding_service.EMPTY_INPUT)
            },
            "pinned": ctx.lease_id is not None,
            "groups": len(groups),
            "rows_dropped": len(events),
            "cross_split_groups": cross[:200],
            "cross_split_group_count": len(cross),
        }
        return OperatorResult(output=table.filter(pa.array(keep)), events=events, report=report)
