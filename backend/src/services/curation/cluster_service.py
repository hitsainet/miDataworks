"""Clusters on a lexical basis, and assignment into an existing clustering (FR-004.24, 004.51; P-18).

Lexical basis: ``HashingVectorizer`` (stateless, so it needs no stored vocabulary) -> TF-IDF ->
``MiniBatchKMeans`` seeded with the step seed. The result records ``basis="lexical"``, ``k``, and the
vectoriser parameters beside every figure. The model (IDF weights and centroids) is stored as a
``cluster_model.npz`` artefact, so :func:`assign_to_clusters` places rows of ANOTHER version into
these clusters without refitting (feature 007's coverage measure, R-03.37).

Deviation (FTID §7.9 named ``n_features=2**18``): ``2**15`` features keep the stored centroids small
(50 clusters x 32,768 x 4 bytes = 6.5 MB) while assignment still reads the artefact, never a refit.
The embedding basis waits for phase 8 (P-18); a report with an unreproducible basis is refused.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from sklearn.cluster import MiniBatchKMeans
from sklearn.feature_extraction.text import HashingVectorizer, TfidfTransformer
from sklearn.preprocessing import normalize
from sqlalchemy.orm import Session

from ...core.storage import resolve_under_data_dir, run_dir, staged_path
from ...models.enums import ColumnRole
from ..identity import file_sha256
from .codes import ReportInput, files_for, load_table, schema_names
from .errors import CurationError
from .text_stats import as_text

N_FEATURES = 2**15
DEFAULT_K = 50
MODEL_FILE = "cluster_model.npz"
ASSIGN_FILE = "cluster_assignments.parquet"
VECTORISER = {
    "kind": "hashing_tfidf",
    "n_features": N_FEATURES,
    "alternate_sign": False,
    "ngram_range": [1, 1],
    "lowercase": True,
}


def vectoriser() -> HashingVectorizer:
    return HashingVectorizer(n_features=N_FEATURES, alternate_sign=False, norm=None)


@dataclass
class Fitted:
    labels: np.ndarray
    distances: np.ndarray
    centroids: np.ndarray
    idf: np.ndarray
    k: int


def _kmeans(
    texts: Sequence[str], k: int, seed: int
) -> tuple[Any, MiniBatchKMeans, TfidfTransformer, np.ndarray, int]:
    k = max(1, min(int(k), len(set(texts)) or 1))
    counts = vectoriser().transform(list(texts))
    tfidf = TfidfTransformer().fit(counts)
    matrix = normalize(tfidf.transform(counts))
    model = MiniBatchKMeans(n_clusters=k, random_state=seed, n_init=3, batch_size=1024)
    labels = model.fit_predict(matrix)
    return matrix, model, tfidf, labels, k


def fit(texts: Sequence[str], k: int, seed: int) -> Fitted:
    """Cluster ``texts`` (k is reduced to the number of distinct rows when there are fewer)."""
    matrix, model, tfidf, labels, k = _kmeans(texts, k, seed)
    centroids = model.cluster_centers_.astype(np.float32)
    distances = _distances(matrix, centroids, labels)
    return Fitted(labels.astype(np.int32), distances, centroids, tfidf.idf_.astype(np.float32), k)


def fit_labels(texts: Sequence[str], k: int, seed: int) -> tuple[np.ndarray, int]:
    """:func:`fit`'s labels and k only: the same clustering, without the per-row distances and
    the float32 centroids a stored model needs. Feature 007's largest-share refit uses it (method
    version 3), on the distinct texts of two samples of ``DIVERSITY_SAMPLE_SIZE`` rows."""
    _, _, _, labels, k = _kmeans(texts, k, seed)
    return labels.astype(np.int32), k


#: Rows densified at a time by :func:`_distances` (1,024 x 32,768 float64 = 268 MB).
DISTANCE_CHUNK = 1024


def _squared_distances(matrix: Any, centroids: np.ndarray) -> np.ndarray:
    """``||x - c||^2`` for every row and centroid, as ``||x||^2 - 2 x.c + ||c||^2`` (the formula
    :func:`assign_matrix` always used) with the rows kept SPARSE: rows x k, not rows x 32,768.

    Pre-existing defect fixed 2026-10-19 (method version 3 review): ``assign_matrix`` densified
    the whole matrix, rows x 32,768 float64 (1.3 GB at 5,000 rows; the 5,000-row diversity
    benchmark peaked at 3.4 GB). Same labels on 25,000 rows of jokes, fixture and Zipf text,
    distances within 1.3e-15; assigning 4,000 jokes 3.28 s -> 0.07 s."""
    if hasattr(matrix, "multiply"):
        rows = np.asarray(matrix.multiply(matrix).sum(axis=1)).reshape(-1, 1)
    else:
        matrix = np.asarray(matrix, dtype=np.float64)
        rows = (matrix * matrix).sum(axis=1, keepdims=True)
    dots = np.asarray(matrix @ centroids.T.astype(np.float64))
    out: np.ndarray = rows - 2 * dots + (centroids * centroids).sum(axis=1)
    return out


def _distances(matrix: Any, centroids: np.ndarray, labels: np.ndarray) -> np.ndarray:
    """Each row's distance to its own centroid, computed directly (no cancellation), densifying
    ``DISTANCE_CHUNK`` rows at a time instead of the whole matrix (same values, bounded memory;
    pre-existing defect fixed 2026-10-19)."""
    n = matrix.shape[0]
    out = np.empty(n, dtype=np.float64)
    for start in range(0, n, DISTANCE_CHUNK):
        part = matrix[start : start + DISTANCE_CHUNK]
        dense_rows = part.toarray() if hasattr(part, "toarray") else np.asarray(part)
        diff = dense_rows - centroids[labels[start : start + DISTANCE_CHUNK]]
        out[start : start + DISTANCE_CHUNK] = np.sqrt((diff * diff).sum(axis=1))
    return out


def assign_matrix(
    texts: Sequence[str], idf: np.ndarray, centroids: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Nearest stored centroid for each text, using the STORED idf (no refit)."""
    counts = vectoriser().transform(list(texts))
    matrix = normalize(counts.multiply(idf.reshape(1, -1)).tocsr())
    d2 = _squared_distances(matrix, centroids)
    labels = np.argmin(d2, axis=1).astype(np.int32)
    distances = np.sqrt(np.maximum(d2[np.arange(len(labels)), labels], 0.0))
    return labels, distances


def texts_of(table: pa.Table, content: Sequence[str]) -> list[str]:
    columns = [table.column(c).to_pylist() for c in content if c in table.schema.names]
    if not columns:
        return [""] * table.num_rows
    return ["\n".join(as_text(v) for v in vals) for vals in zip(*columns, strict=True)]


def summary(table: pa.Table, texts: Sequence[str], fitted: Fitted) -> dict[str, Any]:
    from .audit_service import excerpt

    keys = table.column("_dw_row_key").to_pylist()
    sizes = np.bincount(fitted.labels, minlength=fitted.k)
    exemplars = []
    for c in range(fitted.k):
        members = np.flatnonzero(fitted.labels == c)
        if members.size == 0:
            continue
        best = int(members[np.argmin(fitted.distances[members])])
        exemplars.append(
            {
                "cluster": c,
                "size": int(sizes[c]),
                "row_key": keys[best],
                "excerpt": excerpt(texts[best]),
            }
        )
    return {
        "basis": "lexical",
        "method": "minibatch_kmeans",
        "k": fitted.k,
        "vectoriser": VECTORISER,
        "n_rows": table.num_rows,
        "sizes": sorted((int(s) for s in sizes), reverse=True),
        "exemplars": exemplars,
    }


def compute_clusters(
    session: Session, inputs: list[ReportInput], params: dict[str, Any], seed: int
) -> Any:
    from .api import require_version
    from .report_service import Computed

    tables, content = [], set()
    for ri in inputs:
        version = require_version(session, ri.version_id)
        content |= {c for c, r in version.column_roles.items() if r == ColumnRole.CONTENT}
        source = files_for(version, ri)
        names = schema_names([source])
        tables.append(
            load_table(
                [source],
                ["_dw_row_key", "_dw_occurrence", *sorted(c for c in content if c in names)],
            )
        )
    table = pa.concat_tables(tables, promote_options="default")
    texts = texts_of(table, sorted(content))
    if not texts:
        raise CurationError("insufficient_rows", "The version has no rows to cluster.")
    fitted = fit(texts, int(params.get("k") or DEFAULT_K), seed)
    result = summary(table, texts, fitted)
    folder = uuid.uuid4().hex
    base = run_dir(folder) / "reports"
    base.mkdir(parents=True, exist_ok=True)
    model = base / MODEL_FILE
    with staged_path(model) as staged:
        with open(staged, "wb") as fh:
            np.savez_compressed(
                fh, centroids=fitted.centroids, idf=fitted.idf, k=np.array([fitted.k])
            )
    assigned = base / ASSIGN_FILE
    with staged_path(assigned) as staged:
        pq.write_table(
            pa.table(
                {
                    "_dw_row_key": table.column("_dw_row_key"),
                    "_dw_occurrence": table.column("_dw_occurrence"),
                    "cluster": pa.array(fitted.labels),
                    "distance": pa.array(fitted.distances),
                }
            ),
            staged,
        )
    artefacts = [
        {
            "name": MODEL_FILE,
            "path": f"runs/{folder}/reports/{MODEL_FILE}",
            "sha256": file_sha256(model),
            "rows": fitted.k,
        },
        {
            "name": ASSIGN_FILE,
            "path": f"runs/{folder}/reports/{ASSIGN_FILE}",
            "sha256": file_sha256(assigned),
            "rows": table.num_rows,
        },
    ]
    result["cluster_model"] = artefacts[0]["path"]
    return Computed(result=result, artefacts=artefacts, rows=table.num_rows)


def assign_to_clusters(report_id: str, rows: Any) -> list[dict[str, Any]]:
    """Place ``rows`` (texts, or dicts/a table with the report's content columns) into a stored
    clustering. Reads the ``cluster_model`` artefact; never refits (FR-004.51)."""
    from ...core.database import sync_session_factory
    from ...models.curation import VersionReport

    with sync_session_factory()() as session:
        report = session.get(VersionReport, report_id)
        if report is None or report.kind != "clusters" or report.state != "completed":
            raise CurationError("report_not_found", f"No completed clusters report {report_id}.")
        result = dict(report.result or {})
    basis = result.get("basis")
    if basis != "lexical" or result.get("vectoriser") != VECTORISER:
        raise CurationError(
            "basis_unreproducible",
            f"This clustering's basis ({basis}) cannot be reproduced here: "
            + (
                "the embeddings role is not available."
                if basis == "embedding"
                else "its vectoriser differs from this build's."
            ),
            {"basis": basis},
        )
    with np.load(resolve_under_data_dir(result["cluster_model"])) as model:
        centroids, idf = model["centroids"], model["idf"]
    if isinstance(rows, pa.Table):
        texts = texts_of(rows, [c for c in rows.schema.names if not c.startswith("_dw_")])
    else:
        texts = [
            r if isinstance(r, str) else "\n".join(as_text(v) for v in r.values()) for r in rows
        ]
    labels, distances = assign_matrix(texts, idf, centroids)
    return [
        {"cluster": int(c), "distance": float(d)} for c, d in zip(labels, distances, strict=True)
    ]
