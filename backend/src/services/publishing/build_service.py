"""Publish builds: one Parquet file per split, written once and hashed from disk (FR-008.3,
FR-008.20, FR-008.27, FR-008.28, FR-008.65; FTDD 008 section 2.2 step 1).

A build is content-addressed by (version, projection digest). A matching completed build is
reused; a matching queued or running one is returned rather than duplicated.

The worker streams each split in batches of ``PUBLISH_BUILD_BATCH_ROWS`` through a
``ParquetWriter`` into ``staging/`` and renames it into ``publish/<build_id>/data/<split>.parquet``;
memory is bounded by the batch, not the split (FTASKS 5.5). Every digest is computed from the
renamed file's bytes, which are the bytes a publish uploads (FR-008.20).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.clock import utc_now
from ...core.config import get_settings
from ...core.errors import AppError, ConflictError, NotFoundError
from ...core.ids import new_id
from ...core.storage import publish_dir, resolve_under_data_dir, staged_path
from ...models.enums import VersionState
from ...models.job import Job
from ...models.publish import BuildStatus, PublishBuild
from ...models.version import Version
from ..identity import logical_digest
from . import feature_seams
from .digests import file_sha256, git_blob_sha1
from .projection import (
    Counters,
    ProjectionError,
    apply_effective_labels,
    describe_columns,
    kept_columns,
    projection_digest,
    projection_spec,
)

logger = logging.getLogger(__name__)

DATA_PREFIX = "data"


def repo_path_for_split(file_name: str) -> str:
    """Where a split lives in the repository and inside ``publish/<build_id>/``."""
    return f"{DATA_PREFIX}/{file_name}"


def version_or_refuse(session: Session, version_id: str) -> Version:
    try:
        row = session.get(Version, version_id)
    except Exception:  # noqa: BLE001 - a malformed UUID is "no such version", not a 500
        session.rollback()
        row = None
    if row is None:
        raise NotFoundError(f"No version {version_id}.", code="version_not_found")
    if row.state != VersionState.COMPLETED:
        raise ConflictError(
            f"Version {version_id} is {row.state}; only a completed version can be published "
            "or exported.",
            code="version_not_complete",
            details={"state": row.state},
        )
    return row


def check_label_column(version: Version, label_column: str | None) -> None:
    if label_column is None:
        return
    if label_column.startswith("_dw_") or label_column not in version.column_roles:
        raise AppError(
            f"The version has no column {label_column!r} to use as its label. Choose one of "
            f"{sorted(c for c in version.column_roles if not c.startswith('_dw_'))}.",
            code="label_column_unknown",
            status_code=422,
            details={"columns": sorted(version.column_roles)},
        )


@dataclass(frozen=True)
class BuildRequestOutcome:
    build: PublishBuild
    job: Job | None
    reused: bool


def request_build(
    session: Session,
    version_id: str,
    label_column: str | None,
    *,
    started_by: str,
    origin: str,
) -> BuildRequestOutcome:
    """Reuse, join or start a build. The caller dispatches the job when one was created."""
    version = version_or_refuse(session, version_id)
    check_label_column(version, label_column)
    spec = projection_spec(
        version.id, label_column, review_layer=feature_seams.effective_labels_available()
    )
    digest = projection_digest(spec)
    existing = session.execute(
        select(PublishBuild)
        .where(
            PublishBuild.version_id == version.id,
            PublishBuild.projection_digest == digest,
            PublishBuild.status.in_(
                (BuildStatus.COMPLETED, BuildStatus.QUEUED, BuildStatus.BUILDING)
            ),
        )
        .order_by(PublishBuild.created_at.desc())
    ).scalars()
    for build in existing:
        return BuildRequestOutcome(build, None, reused=build.status == BuildStatus.COMPLETED)
    build_id = new_id("pbld")
    job = Job(
        id=new_id("job"),
        kind="publish_build",
        status="queued",
        progress=0.0,
        params={"build_id": build_id},
        started_by=started_by,
        started_by_origin=origin,
    )
    session.add(job)
    session.flush()
    build = PublishBuild(
        id=build_id,
        version_id=version.id,
        projection_digest=digest,
        projection=spec,
        job_id=job.id,
        status=BuildStatus.QUEUED,
        started_by=started_by,
        started_by_origin=origin,
    )
    session.add(build)
    session.commit()
    return BuildRequestOutcome(build, job, reused=False)


def completed_build(session: Session, build_id: str, version_id: str | None = None) -> PublishBuild:
    build = session.get(PublishBuild, build_id)
    if build is None:
        raise NotFoundError(f"No publish build {build_id}.", code="build_not_found")
    if version_id is not None and build.version_id != version_id:
        raise ConflictError(
            f"Build {build_id} belongs to another version.", code="build_version_mismatch"
        )
    if build.status != BuildStatus.COMPLETED or build.files is None:
        raise ConflictError(
            f"Build {build_id} is {build.status}; a publish needs a completed build.",
            code="version_not_complete",
            details={"build_status": build.status},
        )
    return build


# --- the worker half --------------------------------------------------------------------------

#: Seam for the streaming test: the writer class a build uses.
WRITER: Callable[..., Any] = pq.ParquetWriter


def write_split(
    source: Path,
    destination: Path,
    label_column: str | None,
    resolved: dict[str, dict[str, Any]] | None,
    counters: Counters,
    batch_rows: int,
    on_batch: Callable[[], None] | None = None,
) -> pa.Schema:
    """Stream ``source`` into ``destination`` (staged then renamed). Returns the written schema."""
    handle = pq.ParquetFile(source)
    columns = kept_columns(handle.schema_arrow.names)
    schema = pa.schema([handle.schema_arrow.field(c) for c in columns])
    with staged_path(destination) as staged:
        writer = WRITER(staged, schema, compression="zstd")
        try:
            for batch in handle.iter_batches(batch_size=batch_rows, columns=columns):
                projected = apply_effective_labels(batch, label_column, resolved, counters)
                writer.write_batch(projected)
                if on_batch is not None:
                    on_batch()
        finally:
            writer.close()
    return schema


def run_build(
    session: Session,
    build_id: str,
    *,
    on_batch: Callable[[], None] | None = None,
) -> PublishBuild:
    """Write every split, hash it, and complete the build row."""
    build = session.get(PublishBuild, build_id)
    if build is None:
        raise NotFoundError(f"No publish build {build_id}.", code="build_not_found")
    version = version_or_refuse(session, build.version_id)
    build.status = BuildStatus.BUILDING
    session.commit()
    label_column = build.projection["label_column"]
    review_layer = bool(build.projection["review_layer"])
    batch_rows = get_settings().publish_build_batch_rows
    root = publish_dir(build.id)
    files: list[dict[str, Any]] = []
    totals = Counters()
    shipped_schema: pa.Schema | None = None
    for split in version.splits:
        source = resolve_under_data_dir(split["path"])
        name = str(split["name"])
        file_name = Path(split["path"]).name
        destination = root / DATA_PREFIX / file_name
        resolved = None
        if review_layer and label_column is not None:
            keys = pq.read_table(source, columns=["_dw_row_key"]).column(0).to_pylist()
            resolved = feature_seams.resolve_effective_labels(version.id, None, None, keys)
        counters = Counters()
        try:
            schema = write_split(
                source, destination, label_column, resolved, counters, batch_rows, on_batch
            )
        except ProjectionError as exc:
            raise AppError(
                exc.message, code=exc.code, status_code=409, details=exc.details
            ) from exc
        shipped_schema = shipped_schema or schema
        totals.omitted_excluded += counters.omitted_excluded
        totals.omitted_flagged_unresolved += counters.omitted_flagged_unresolved
        totals.overrides_applied += counters.overrides_applied
        files.append(
            {
                "name": name,
                "path": repo_path_for_split(file_name),
                "rows": pq.ParquetFile(destination).metadata.num_rows,
                "bytes": destination.stat().st_size,
                "sha256": file_sha256(destination),
                "git_blob_sha1": git_blob_sha1(destination),
                "logical_digest": logical_digest(destination),
                "label_counts": dict(sorted(counters.label_counts.items())),
                "held_out": bool(split["held_out"]),
                "evaluation_only": bool(split["held_out"]),
            }
        )
    label_values = sorted({k for f in files for k in f["label_counts"]}) if label_column else None
    columns = (
        describe_columns(shipped_schema, version.column_roles, label_column, label_values)
        if shipped_schema is not None
        else []
    )
    build.files = files
    build.columns = columns
    build.omitted = {
        "excluded": totals.omitted_excluded,
        "flagged_unresolved": totals.omitted_flagged_unresolved,
        "overrides_applied": totals.overrides_applied,
    }
    build.status = BuildStatus.COMPLETED
    build.completed_at = utc_now()
    session.commit()
    logger.info(
        "publish_build %s completed: %s",
        build.id,
        json.dumps({f["name"]: f["rows"] for f in files}),
    )
    return build


def build_file_path(build: PublishBuild, repo_path: str) -> Path:
    """The local file for a split's repository path, confined under the build's directory."""
    return resolve_under_data_dir(publish_dir(build.id) / repo_path)
