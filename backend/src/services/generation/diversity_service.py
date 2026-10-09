"""The diversity report: figures, intervals, controls and a verdict (FTDD 007 section 6.6).

FR-007.38 – FR-007.43; P-17, P-18, P-01; T-36.

```
reference  ← nearest ancestor (002 lineage) with no generated row (P-17)
column     ← request, or by target (sft/kto: completion; grpo_prompt: prompt; dpo: chosen)
comparable ← same column present, same non-held-out split names       → else DIVERSITY_INCOMPARABLE
sample     ← ≤ DIVERSITY_SAMPLE_SIZE rows per side from non-held-out splits, seeded
distinct   ← distinct-1, distinct-2 (lex-v1), bootstrap 95% intervals
spread     ← embeddings role (005 resolve) → mean cosine distance to the centroid (T-36);
             unconfigured → not_measured (P-18); the two sides' SERVED embedding models must agree
clusters   ← 004's clustering of the reference (lexical basis, recorded) + assign_to_clusters
falls      ← the WHOLE version interval past the reference's (P-17)
controls   ← negative: a collapsed reference sample MUST fall; positive: two halves MUST NOT
verdict    ← invalid (a control failed) | falls | not_measured | holds
```

Deviation from FTDD 007 section 6.6 (recorded): ``falls`` is decided BEFORE ``not_measured``. A
measured fall on distinct-n is a fall whether or not embeddings were configured; ordering it
after would hide a narrowing behind an unconfigured endpoint. A falling verdict refuses nothing
(P-01): 008 shows the caveat ``diversity_falling``.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.canonical_json import canonical_sha256
from ...core.config import get_settings
from ...core.errors import AppError, ConflictError
from ...core.ids import new_id
from ...core.storage import embeddings_cache_dir, resolve_under_data_dir, staged_path
from ...models.dataset import Dataset
from ...models.enums import ColumnRole
from ...models.generation import DiversityReport
from ...models.version import Version
from .. import identity
from ..duck import connect, files_param, quote_ident, top_level_columns
from . import diversity_metrics as dm

logger = logging.getLogger(__name__)

DEFAULT_COLUMN: dict[str, str] = {
    "sft": "completion",
    "kto": "completion",
    "grpo_prompt": "prompt",
    "dpo": "chosen",
}
EMBED_BATCH = 64


class Incomparable(AppError):
    status_code = 409
    code = "DIVERSITY_INCOMPARABLE"


# --- lineage and sides -----------------------------------------------------------------------


def _files(version: Version, splits: Sequence[str] | None = None) -> list[Any]:
    chosen = [s for s in version.splits if splits is None or s["name"] in set(splits)]
    return [resolve_under_data_dir(str(s["path"])) for s in chosen]


def has_generated_rows(version: Version) -> bool:
    files = _files(version)
    if not files:
        return False
    con = connect()
    try:
        columns = top_level_columns(con, files)
        if "_dw_origin" not in columns:
            return False
        row = con.execute(
            "SELECT count(*) FROM read_parquet(?) WHERE _dw_origin = 'generated'",
            [files_param(files)],
        ).fetchone()
        return bool(row and row[0])
    finally:
        con.close()


def reference_for(session: Session, version: Version) -> Version | None:
    """The nearest ancestor (parent chain) whose splits hold no generated row (P-17)."""
    current = version
    seen = {version.id}
    while current.parent_version_id is not None:
        parent = session.get(Version, current.parent_version_id)
        if parent is None or parent.id in seen:
            return None
        seen.add(parent.id)
        if not has_generated_rows(parent):
            return parent
        current = parent
    return None


def non_held_out(version: Version) -> list[str]:
    return sorted(str(s["name"]) for s in version.splits if not s["held_out"])


def _columns(version: Version) -> set[str]:
    files = _files(version)
    con = connect()
    try:
        return set(top_level_columns(con, files)) if files else set()
    finally:
        con.close()


def default_column(session: Session, version: Version) -> str:
    dataset = session.get(Dataset, version.dataset_id)
    assert dataset is not None
    column = DEFAULT_COLUMN.get(dataset.target_type)
    if column is None:
        content = sorted(c for c, r in version.column_roles.items() if r == ColumnRole.CONTENT)
        if not content:
            raise ConflictError("The version has no content column to measure.", code="NO_COLUMN")
        column = content[0]
    return column


def incomparable_reasons(version_meta: dict[str, Any], reference_meta: dict[str, Any]) -> list[str]:
    """Why two sides cannot be compared (FR-007.42): column, splits, embedding identity, clusters."""
    reasons = []
    for key in ("column_present", "splits", "embedding_identity", "clustering"):
        if version_meta.get(key) != reference_meta.get(key):
            reasons.append(key)
    return reasons


def check_comparable(
    session: Session, version: Version, column: str
) -> tuple[Version | None, list[str]]:
    """The reference and the API-side comparability check (column and splits)."""
    reference = reference_for(session, version)
    if reference is None:
        return None, non_held_out(version)
    splits = non_held_out(version)
    reasons = incomparable_reasons(
        {"column_present": column in _columns(version), "splits": splits},
        {"column_present": column in _columns(reference), "splits": non_held_out(reference)},
    )
    if column not in _columns(version):
        reasons.append("column_missing_in_version")
    if reasons:
        raise Incomparable(
            f"Version and reference cannot be compared on {column!r}: {', '.join(reasons)} "
            "differ. Choose a column both have, from the same non-held-out splits.",
            details={"reasons": reasons, "reference_version_id": reference.id, "column": column},
        )
    return reference, splits


def sample_texts(
    version: Version, splits: Sequence[str], column: str, content: Sequence[str], n: int, seed: int
) -> tuple[list[str], list[str]]:
    """(texts of ``column``, cluster texts over the content columns) for a seeded sample."""
    files = _files(version, splits)
    con = connect()
    try:
        columns = top_level_columns(con, files)
        col = quote_ident(column, columns)
        present = [c for c in sorted(content) if c in columns]
        extra = "".join(f", {quote_ident(c, columns)}" for c in present)
        sql = (
            f"SELECT {col} AS __text{extra} FROM read_parquet(?) WHERE {col} IS NOT NULL "  # noqa: S608
            f"USING SAMPLE reservoir({int(n)} ROWS) REPEATABLE ({int(seed)})"
        )
        rows = con.execute(sql, [files_param(files)]).fetchall()
    finally:
        con.close()
    texts = [str(r[0]) for r in rows]
    cluster_texts = ["\n".join("" if v is None else str(v) for v in r[1:]) for r in rows]
    return texts, cluster_texts


# --- embeddings ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Embedded:
    vectors: np.ndarray
    identity: dict[str, Any]


def embed(texts: Sequence[str], session: Session) -> Embedded | None:
    """Embeddings through 005's ``embeddings`` role, cached by (identity, text); None when the
    role is unconfigured (P-18). The identity records the SERVED model (``model`` of the reply)."""
    from ...clients.endpoint_caller import EndpointCaller
    from ..endpoint_resolver import RoleUnconfigured, resolve

    try:
        resolved = resolve("embeddings", session)
    except RoleUnconfigured:
        return None
    settings = get_settings()
    configured = {
        "role_model": resolved.model_id,
        "protocol": resolved.protocol,
        "base_url": resolved.base_url,
    }
    key = canonical_sha256(configured)
    cache = _load_cache(key)
    missing = sorted({t for t in texts if t not in cache})
    if missing:
        with EndpointCaller(
            resolved.base_url, resolved.api_key, timeout_s=settings.endpoint_http_timeout_seconds
        ) as caller:
            for start in range(0, len(missing), EMBED_BATCH):
                batch = missing[start : start + EMBED_BATCH]
                vectors, model = _embed_batch(caller, resolved, batch)
                for text, vector in zip(batch, vectors, strict=True):
                    cache[text] = (vector, model)
        _store_cache(key, cache, missing)
    served = sorted({cache[t][1] for t in texts})
    if len(served) > 1:
        raise Incomparable(
            f"The embeddings endpoint served {served} within one report; the figures would mix "
            "two embedding spaces.",
            details={"reasons": ["embedding_identity"], "served": served},
        )
    matrix = np.asarray([cache[t][0] for t in texts], dtype=np.float64)
    identity_doc = {
        "role_model": resolved.model_id,
        "protocol": resolved.protocol,
        "served_model": served[0] if served else None,
        "pooling": "endpoint",
    }
    return Embedded(matrix, identity_doc)


def _embed_batch(caller: Any, resolved: Any, batch: list[str]) -> tuple[list[list[float]], str]:
    if resolved.protocol == "tei_embeddings":
        response = caller.call(
            "POST", "/embed", body={"inputs": batch}, purpose="probe", openai=False
        )
        body = response.body
        if not isinstance(body, list):
            raise AppError(
                "TEI /embed did not return a list.", code="EMBEDDINGS_INVALID", status_code=502
            )
        return [list(map(float, v)) for v in body], resolved.model_id
    response = caller.call(
        "POST",
        "/v1/embeddings",
        body={"model": resolved.model_id, "input": batch},
        purpose="probe",
        openai=True,
    )
    body = response.body if isinstance(response.body, dict) else {}
    data = sorted(body.get("data") or [], key=lambda d: int(d.get("index", 0)))
    if len(data) != len(batch):
        raise AppError(
            "The embeddings reply has the wrong length.", code="EMBEDDINGS_INVALID", status_code=502
        )
    return [list(map(float, d["embedding"])) for d in data], str(
        body.get("model") or resolved.model_id
    )


def _cache_dir(key: str) -> Any:
    return embeddings_cache_dir(key)


def _load_cache(key: str) -> dict[str, tuple[list[float], str]]:
    directory = _cache_dir(key)
    cache: dict[str, tuple[list[float], str]] = {}
    if not directory.is_dir():
        return cache
    for part in sorted(directory.glob("part-*.parquet")):
        table = pq.read_table(part)
        for text, vector, model in zip(
            table.column("text").to_pylist(),
            table.column("vector").to_pylist(),
            table.column("served_model").to_pylist(),
            strict=True,
        ):
            cache[str(text)] = (vector, str(model))
    return cache


def _store_cache(key: str, cache: dict[str, tuple[list[float], str]], new: Sequence[str]) -> None:
    directory = _cache_dir(key)
    directory.mkdir(parents=True, exist_ok=True)
    part = directory / f"part-{new_id('emb')}.parquet"
    table = pa.table(
        {
            "text": pa.array(list(new), pa.string()),
            "vector": pa.array([cache[t][0] for t in new], pa.list_(pa.float64())),
            "served_model": pa.array([cache[t][1] for t in new], pa.string()),
        }
    )
    with staged_path(part) as staged:
        pq.write_table(table, staged)


# --- clusters --------------------------------------------------------------------------------


def reference_clusters(
    reference: Version, seed: int, k: int, who: str
) -> tuple[str, dict[str, Any]]:
    """004's clustering report of the reference (computed inline when none is stored)."""
    from ..curation import report_service
    from ..curation.cluster_service import compute_clusters
    from ..curation.codes import ReportInput

    _, row = report_service.find_or_run_inline(
        "clusters",
        [ReportInput(str(reference.id))],
        {"k": int(k)},
        seed,
        compute_clusters,
        started_by=who,
        origin="operator",
    )
    result = dict(row.result or {})
    return str(row.id), {
        "report_id": str(row.id),
        "method": result.get("method"),
        "basis": result.get("basis"),
        "k": result.get("k"),
        "vectoriser": result.get("vectoriser"),
    }


def assign(report_id: str, texts: Sequence[str]) -> list[int]:
    from ..curation.api import assign_to_clusters

    return [int(a["cluster"]) for a in assign_to_clusters(report_id, list(texts))]


# --- the report ------------------------------------------------------------------------------


def _figure(
    version: dm.Interval, reference: dm.Interval, direction: dm.Direction
) -> dict[str, Any]:
    falls = dm.falls(version, reference, direction)
    return {
        "version": version.as_dict(),
        "reference": reference.as_dict(),
        "direction": direction,
        "verdict": "falls" if falls else "holds",
        "reason": None,
    }


def _not_measured(reason: str) -> dict[str, Any]:
    return {"version": None, "reference": None, "verdict": "not_measured", "reason": reason}


def figures_for(
    v_texts: Sequence[str],
    r_texts: Sequence[str],
    v_vectors: np.ndarray | None,
    r_vectors: np.ndarray | None,
    v_clusters: Sequence[int] | np.ndarray,
    r_clusters: Sequence[int] | np.ndarray,
    n_clusters: int,
    seed: int,
    resamples: int,
) -> dict[str, Any]:
    """Every figure for one (version, reference) pair of samples. Pure over its inputs."""
    rng = np.random.default_rng(seed)
    # One subsample size for both sides: distinct-n and coverage depend on sample size.
    size = max(2, min(len(v_texts), len(r_texts)) // 2)
    figures: dict[str, Any] = {}
    for n in (1, 2):
        figures[f"distinct_{n}"] = _figure(
            dm.distinct_ci(v_texts, n, rng, resamples, size),
            dm.distinct_ci(r_texts, n, rng, resamples, size),
            "higher_is_diverse",
        )
    if v_vectors is None or r_vectors is None:
        figures["embedding_spread"] = _not_measured(
            "no embeddings endpoint is configured (P-18); spread is not measured"
        )
    else:
        figures["embedding_spread"] = _figure(
            dm.spread_ci(v_vectors, rng, resamples, size),
            dm.spread_ci(r_vectors, rng, resamples, size),
            "higher_is_diverse",
        )
    v_cov, v_share = dm.coverage_ci(v_clusters, n_clusters, rng, resamples, size)
    r_cov, r_share = dm.coverage_ci(r_clusters, n_clusters, rng, resamples, size)
    figures["cluster_coverage"] = _figure(v_cov, r_cov, "higher_is_diverse")
    figures["largest_cluster_share"] = _figure(v_share, r_share, "lower_is_diverse")
    return figures


def controls_for(
    r_texts: Sequence[str],
    r_vectors: np.ndarray | None,
    r_clusters: Sequence[int],
    n_clusters: int,
    seed: int,
    resamples: int,
) -> list[dict[str, Any]]:
    """Negative: a collapsed reference sample MUST fall on distinct-2 (and spread when measured)
    and RISE on the largest-cluster share. Positive: two halves MUST NOT fall on anything."""
    rng = np.random.default_rng(seed + 1)
    n = len(r_texts)
    checks: list[dict[str, Any]] = []
    if n < 4:
        for check_id in ("diversity_negative_control", "diversity_positive_control"):
            checks.append(
                {
                    "check_id": check_id,
                    "version": 1,
                    "result": "fail",
                    "statistic": {"rows": n},
                    "rule": "at least 4 reference rows",
                    "reason": "too few reference rows for a control",
                }
            )
        return checks
    labels = np.asarray(r_clusters, dtype=np.int64)
    collapsed = dm.collapse_sample(n, labels, rng)
    c_texts = [r_texts[int(i)] for i in collapsed]
    c_vectors = None if r_vectors is None else r_vectors[collapsed]
    c_clusters = labels[collapsed]
    negative = figures_for(
        c_texts, r_texts, c_vectors, r_vectors, c_clusters, labels, n_clusters, seed + 2, resamples
    )
    fell = {k: negative[k]["verdict"] == "falls" for k in ("distinct_2", "largest_cluster_share")}
    if r_vectors is not None:
        toward = dm.collapse_toward(r_vectors, rng)
        spread = _figure(
            dm.spread_ci(r_vectors[toward], rng, resamples),
            dm.spread_ci(r_vectors, rng, resamples),
            "higher_is_diverse",
        )
        fell["embedding_spread"] = spread["verdict"] == "falls"
    checks.append(
        {
            "check_id": "diversity_negative_control",
            "version": 1,
            "result": "pass" if all(fell.values()) else "fail",
            "statistic": fell,
            "rule": "a sample half-replaced by copies from the largest cluster (lexical figures) "
            "or from one row's nearest neighbours (spread) must fall",
            "reason": None if all(fell.values()) else "the figures did not see a collapsed sample",
        }
    )
    half_a, half_b = dm.split_halves(n, rng)
    positive = figures_for(
        [r_texts[int(i)] for i in half_a],
        [r_texts[int(i)] for i in half_b],
        None if r_vectors is None else r_vectors[half_a],
        None if r_vectors is None else r_vectors[half_b],
        labels[half_a],
        labels[half_b],
        n_clusters,
        seed + 3,
        resamples,
    )
    false_falls = [k for k, f in positive.items() if f["verdict"] == "falls"]
    checks.append(
        {
            "check_id": "diversity_positive_control",
            "version": 1,
            "result": "fail" if false_falls else "pass",
            "statistic": {"falls": false_falls},
            "rule": "two halves of the reference must not fall against each other",
            "reason": f"noise read as narrowing on {false_falls}" if false_falls else None,
        }
    )
    return checks


def verdict_of(figures: dict[str, Any], checks: Sequence[dict[str, Any]]) -> str:
    if any(c["result"] == "fail" for c in checks):
        return "invalid"
    if any(f["verdict"] == "falls" for f in figures.values()):
        return "falls"
    if any(f["verdict"] == "not_measured" for f in figures.values()):
        return "not_measured"
    return "holds"


def method_for(
    column: str,
    splits: Sequence[str],
    sample: int,
    seed: int,
    resamples: int,
    k: int,
    embedding: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "column": column,
        "splits": list(splits),
        "sample_size": sample,
        "seed": seed,
        "resamples": resamples,
        "tokeniser": dm.TOKENISER,
        "interval": "half_subsample_percentile_v1",
        "clustering": {"basis": "lexical", "k": k},
        "embedding": embedding,
        "spread": "mean_cosine_to_centroid_v1",
    }


def report_seed(version_id: str) -> int:
    return identity.response_seed(0, f"diversity:{version_id}", 0)


def compute(
    session: Session, version_id: str, column: str, *, job_id: str | None, who: str, origin: str
) -> DiversityReport:
    """Compute and write ONE immutable report row (an existing identical one is returned)."""
    settings = get_settings()
    version = session.get(Version, version_id)
    assert version is not None
    seed = report_seed(version.id)
    n = settings.diversity_sample_size
    resamples = settings.diversity_bootstrap_resamples
    k = settings.diversity_cluster_k
    reference, splits = check_comparable(session, version, column)
    if reference is None:
        method = method_for(column, splits, n, seed, resamples, k, None)
        return _write(
            session,
            version,
            None,
            column,
            method,
            splits,
            n,
            seed,
            None,
            {"basis": None},
            {},
            [],
            "not_measured",
            "No ancestor without generated rows: there is no reference to compare with (P-17).",
            job_id,
            who,
            origin,
        )
    content = sorted(c for c, r in version.column_roles.items() if r == ColumnRole.CONTENT)
    v_texts, v_cluster_texts = sample_texts(version, splits, column, content, n, seed)
    r_texts, r_cluster_texts = sample_texts(reference, splits, column, content, n, seed)
    v_emb = embed(v_texts, session) if v_texts else None
    r_emb = embed(r_texts, session) if r_texts and v_emb is not None else None
    embedding_identity = None
    if v_emb is not None and r_emb is not None:
        reasons = incomparable_reasons(
            {"embedding_identity": v_emb.identity}, {"embedding_identity": r_emb.identity}
        )
        if reasons:
            raise Incomparable(
                "Version and reference were embedded by different models; the spreads are not "
                "comparable.",
                details={
                    "reasons": reasons,
                    "version": v_emb.identity,
                    "reference": r_emb.identity,
                },
            )
        embedding_identity = v_emb.identity
    report_id, clustering = reference_clusters(reference, seed, k, who)
    n_clusters = int(clustering.get("k") or 1)
    v_clusters = assign(report_id, v_cluster_texts) if v_cluster_texts else []
    r_clusters = assign(report_id, r_cluster_texts) if r_cluster_texts else []
    figures = figures_for(
        v_texts,
        r_texts,
        None if v_emb is None else v_emb.vectors,
        None if r_emb is None else r_emb.vectors,
        v_clusters,
        r_clusters,
        n_clusters,
        seed,
        resamples,
    )
    checks = controls_for(
        r_texts, None if r_emb is None else r_emb.vectors, r_clusters, n_clusters, seed, resamples
    )
    verdict = verdict_of(figures, checks)
    method = method_for(column, splits, n, seed, resamples, k, embedding_identity)
    reason = None
    if verdict == "not_measured":
        reason = "Spread is not measured without an embeddings endpoint; distinct-n and lexical clusters are (P-18)."
    elif verdict == "falls":
        reason = "A figure's whole interval fell below the reference's: cap the largest cluster or generate from more seed rows."
    elif verdict == "invalid":
        reason = "A control failed, so the figures cannot be trusted on this data."
    return _write(
        session,
        version,
        reference.id,
        column,
        method,
        splits,
        max(len(v_texts), len(r_texts)),
        seed,
        embedding_identity,
        {**clustering, "basis": clustering.get("basis") or "lexical"},
        figures,
        checks,
        verdict,
        reason,
        job_id,
        who,
        origin,
    )


def _write(
    session: Session,
    version: Version,
    reference_id: str | None,
    column: str,
    method: dict[str, Any],
    splits: Sequence[str],
    sample: int,
    seed: int,
    embedding: dict[str, Any] | None,
    clustering: dict[str, Any],
    figures: dict[str, Any],
    checks: Sequence[dict[str, Any]],
    verdict: str,
    reason: str | None,
    job_id: str | None,
    who: str,
    origin: str,
) -> DiversityReport:
    digest = canonical_sha256(method)
    existing = session.execute(
        select(DiversityReport).where(
            DiversityReport.version_id == version.id,
            DiversityReport.column == column,
            DiversityReport.method_hash == digest,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    row = DiversityReport(
        id=new_id("dr"),
        version_id=version.id,
        reference_version_id=reference_id,
        column=column,
        method_hash=digest,
        method=method,
        splits=list(splits),
        sample_size=int(sample),
        seed=int(seed),
        embedding_identity=embedding,
        clustering=clustering,
        figures=figures,
        checks=list(checks),
        verdict=verdict,
        reason=reason,
        job_id=job_id,
        created_by=who,
        created_by_origin=origin,
    )
    session.add(row)
    session.commit()
    logger.info(
        "diversity_report.written version=%s column=%s verdict=%s", version.id, column, verdict
    )
    return row


def latest(session: Session, version_id: str, column: str | None) -> DiversityReport | None:
    query = select(DiversityReport).where(DiversityReport.version_id == version_id)
    if column is not None:
        query = query.where(DiversityReport.column == column)
    return session.execute(
        query.order_by(DiversityReport.created_at.desc(), DiversityReport.id.desc()).limit(1)
    ).scalar_one_or_none()


# --- API side and the sweep -------------------------------------------------------------------

JOB_KIND = "diversity_report"


def live_job(session: Session, version_id: str, column: str) -> str | None:
    from ...models.job import Job

    rows = session.execute(
        select(Job.id, Job.params).where(
            Job.kind == JOB_KIND, Job.status.in_(("queued", "running", "cancelling"))
        )
    ).all()
    for job_id, params in rows:
        if params.get("version_id") == version_id and params.get("column") == column:
            return str(job_id)
    return None


def plan_request(session: Session, version_id: str, column: str | None) -> tuple[Version, str]:
    """What ``POST /versions/{id}/diversity`` checks before a job exists."""
    from ...models.enums import VersionState

    version = session.get(Version, version_id)
    if version is None:
        raise AppError(f"No version {version_id}.", code="version_not_found", status_code=404)
    if version.state != VersionState.COMPLETED:
        raise ConflictError(f"Version {version_id} is {version.state}.", code="VERSION_NOT_USABLE")
    chosen = column or default_column(session, version)
    check_comparable(session, version, chosen)
    return version, chosen


def create_job(session: Session, version_id: str, column: str, who: str, origin: str) -> str:
    from ...core.ids import new_id as _new
    from ...models.job import Job

    existing = live_job(session, version_id, column)
    if existing is not None:
        return existing
    job = Job(
        id=_new("job"),
        kind=JOB_KIND,
        status="queued",
        progress=0.0,
        params={"version_id": version_id, "column": column},
        started_by=who,
        started_by_origin=origin,
    )
    session.add(job)
    session.commit()
    return job.id


def versions_needing_reports(session: Session, limit: int = 100) -> list[tuple[str, str]]:
    """Completed versions holding generated rows (by lineage binding) with no report for their
    default column and no live job — what the Beat sweep queues (idempotent)."""
    from ...models.enums import VersionState
    from .independence import bound_generation_runs

    out: list[tuple[str, str]] = []
    versions = session.execute(
        select(Version)
        .where(Version.state == VersionState.COMPLETED)
        .order_by(Version.created_at.desc())
        .limit(limit * 5)
    ).scalars()
    for version in versions:
        if not bound_generation_runs(session, version.id):
            continue
        try:
            column = default_column(session, version)
        except AppError:
            continue
        if latest(session, version.id, column) is not None:
            continue
        if live_job(session, version.id, column) is not None:
            continue
        out.append((version.id, column))
        if len(out) >= limit:
            break
    return out
