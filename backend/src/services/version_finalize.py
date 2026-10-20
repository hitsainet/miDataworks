"""Finalize: per-split files, digests, invariants, manifest, rename, commit (FR-002.9, 002.31,
002.34, 002.35, 002.46, 002.49; FTID 002 section 3.7).

The order is pinned by a mutation control, because each step protects the next:

    write split files -> digests -> held-out invariant -> duplicate-metadata scan -> manifest
    -> fsync -> rename into versions/<id>/ -> commit the row -> enqueue post_version

The directory is renamed BEFORE the row commits, so a committed row always has its files. A failed
commit leaves an orphan directory with no row, which ``version_orphan_sweeper`` removes; a reader
never finds a row without files. ``post_version`` (feature 004's audit) is enqueued only after the
commit returns, so a rolled-back version is never audited.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..core.storage import resolve_under_data_dir, staging_dir, version_dir
from ..models.dataset import Dataset
from ..models.enums import ColumnRole, InputKind, Origin, VersionState
from ..models.job import Job
from ..models.source import Source
from ..models.step_execution import StepExecution
from ..models.version import Version, VersionBuild, VersionInput, VersionStep
from .assembly import BuildRefusal
from .duck import connect, files_param, quote_ident
from .identity import bytes_sha256, file_sha256, logical_digest
from .manifest import build_manifest, manifest_bytes
from .step_contract import part_files

logger = logging.getLogger(__name__)

POST_VERSION_TASK = "midataworks.curation.post_version"
_SAFE_SPLIT = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


@dataclass(frozen=True)
class LinkedStep:
    index: int
    execution: StepExecution
    reused: bool


def _fsync(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def split_file_name(split: str, position: int) -> str:
    return f"{split}.parquet" if _SAFE_SPLIT.fullmatch(split) else f"split_{position}.parquet"


def write_split_files(parts: Sequence[Path], directory: Path) -> list[tuple[str, Path]]:
    """One file per ``_dw_split`` value, rows in the last step's order, zstd (FR-002.32)."""
    rows_per_group = get_settings().parquet_row_group_rows
    writers: dict[str, pq.ParquetWriter] = {}
    paths: dict[str, Path] = {}
    order: list[str] = []
    try:
        for part in parts:
            handle = pq.ParquetFile(part)
            for batch in handle.iter_batches(batch_size=rows_per_group):
                table = pa.Table.from_batches([batch])
                splits = table.column("_dw_split").to_pylist()
                for split in dict.fromkeys(splits):
                    mask = pa.array([s == split for s in splits])
                    if split not in writers:
                        order.append(split)
                        paths[split] = directory / split_file_name(split, len(order) - 1)
                        writers[split] = pq.ParquetWriter(
                            paths[split], table.schema, compression="zstd"
                        )
                    writers[split].write_table(table.filter(mask), row_group_size=rows_per_group)
    finally:
        for writer in writers.values():
            writer.close()
    for path in paths.values():
        _fsync(path)
    return [(s, paths[s]) for s in order]


def held_out_violations(path: Path) -> list[str]:
    table = pq.read_table(path, columns=["_dw_row_key", "_dw_origin"])
    origins = table.column("_dw_origin").to_pylist()
    keys = table.column("_dw_row_key").to_pylist()
    return [k for k, o in zip(keys, origins, strict=True) if o == Origin.GENERATED][:20]


def duplicate_metadata_warning(
    files: Sequence[Path], roles: dict[str, str]
) -> dict[str, Any] | None:
    """FR-002.46 / T-07: keys whose copies disagree on any metadata column."""
    metadata = [c for c, r in roles.items() if r == ColumnRole.METADATA]
    if not metadata or not files:
        return None
    con = connect()
    try:
        columns = [
            d[0]
            for d in con.execute(
                "SELECT * FROM read_parquet(?) LIMIT 0", [files_param(files)]
            ).description
            or []
        ]
        disagreeing: dict[str, int] = {}
        for column in metadata:
            if column not in columns:
                continue
            ident = quote_ident(column, columns)
            # ``ident`` is checked against the files' own columns and quoted by quote_ident.
            having = (
                f"count(DISTINCT CAST({ident} AS VARCHAR)) + "
                f"max(CASE WHEN {ident} IS NULL THEN 1 ELSE 0 END) > 1"
            )
            sql = (
                "SELECT count(*) FROM (SELECT _dw_row_key FROM read_parquet(?) "  # noqa: S608
                f"GROUP BY _dw_row_key HAVING {having})"
            )
            n = con.execute(sql, [files_param(files)]).fetchone()
            if n and n[0]:
                disagreeing[column] = int(n[0])
    finally:
        con.close()
    if not disagreeing:
        return None
    worst = max(disagreeing.values())
    return {
        "code": "duplicate_metadata_disagrees",
        "message": (
            f"Copies of {worst} row key(s) disagree on {sorted(disagreeing)}. Labels apply to "
            "every copy; check whether the copies really are the same row."
        ),
        "details": {"columns": disagreeing},
    }


def transitive_sources(session: Session, inputs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every source reached through the lineage, with its pin and licence as 001 recorded it
    (FR-002.8), including "not stated"."""
    found: dict[str, dict[str, Any]] = {}
    for item in inputs:
        if item["kind"] == InputKind.SOURCE:
            source = session.get(Source, item["source_id"])
            assert source is not None
            found.setdefault(
                source.id,
                {
                    "source_id": source.id,
                    "kind": source.kind,
                    "display_name": source.display_name,
                    "repo_id": source.repo_id,
                    "config": source.config,
                    "revision": source.resolved_commit,
                    "content_hash": source.content_hash,
                    "licence_raw": source.licence_raw,
                    "licence_display": source.licence_display,
                    "licence_origin": source.licence_origin,
                },
            )
        else:
            version = session.get(Version, item["version_id"])
            assert version is not None
            for entry in json.loads(version.manifest).get("sources", []):
                found.setdefault(entry["source_id"], entry)
    return [found[k] for k in sorted(found)]


def drop_summary(steps: Sequence[LinkedStep]) -> list[dict[str, Any]]:
    """Counts by step, operator and reason, in step order, with the running row count."""
    summary: list[dict[str, Any]] = []
    for linked in steps:
        exe = linked.execution
        if exe.kind != "operator":
            continue
        rows_in = int(exe.rows_in or 0)
        rows_out = rows_in - int(exe.rows_dropped or 0) + int(exe.rows_added or 0)
        summary.append(
            {
                "step_index": linked.index,
                "operator": exe.operator_name,
                "operator_version": exe.operator_version,
                "reused": linked.reused,
                "rows_in": rows_in,
                "rows_out": rows_out,
                "dropped": int(exe.rows_dropped or 0),
                "changed": int(exe.rows_changed or 0),
                "added": int(exe.rows_added or 0),
                "split_assigned": int(exe.rows_split_assigned or 0),
                "reasons": list(exe.reason_counts or []),
            }
        )
    return summary


def held_out_origin(
    session: Session,
    steps: Sequence[LinkedStep],
    held_out: set[str],
    version_id: str,
    parent: str | None,
) -> str | None:
    """FR-002.31: this version if its own steps declared the held-out split, else the parent's
    value when the parent has a held-out split of the same name, else null."""
    if not held_out:
        return None
    for linked in steps:
        roles = linked.execution.split_roles or {}
        if any(name in held_out and spec.get("held_out") for name, spec in roles.items()):
            return version_id
    if parent is not None:
        row = session.get(Version, parent)
        if row is not None and any(s["held_out"] and s["name"] in held_out for s in row.splits):
            return row.held_out_origin_version_id
    return None


def finalize(
    session: Session,
    job: Job,
    build: VersionBuild,
    steps: Sequence[LinkedStep],
    recipe_body: dict[str, Any],
    *,
    send_task: Any,
) -> str:
    """Write, check, rename and commit the version. Returns its id."""
    request = build.request
    last = steps[-1].execution
    parts = part_files(resolve_under_data_dir(last.output_dir))
    roles = dict(last.output_column_roles or request["initial_roles"])
    # A child keeps its parent's held-out marks unless one of its own steps re-declares the splits.
    # FTID 002 section 3.7 says "otherwise every split has held_out: false"; read literally, a
    # child of a split version would lose its held-out split and with it the held-out invariant
    # (FR-002.31), so generated rows could enter the test split one version later.
    split_roles: dict[str, Any] = {}
    parent_id = next(
        (i["version_id"] for i in request["inputs"] if i["kind"] == InputKind.VERSION), None
    )
    if parent_id is not None:
        parent_row = session.get(Version, parent_id)
        if parent_row is not None:
            split_roles = {s["name"]: {"held_out": bool(s["held_out"])} for s in parent_row.splits}
    for linked in steps:
        if linked.execution.split_roles:
            split_roles = dict(linked.execution.split_roles)  # the latest declaring step wins
    version_id = str(uuid.uuid4())
    staged = staging_dir() / f"version-{version_id}"
    staged.mkdir(parents=True)
    try:
        written = write_split_files(parts, staged)
        splits: list[dict[str, Any]] = []
        held_out: set[str] = set()
        for name, path in written:
            is_held_out = bool(split_roles.get(name, {}).get("held_out", False))
            if is_held_out:
                held_out.add(name)
                generated = held_out_violations(path)
                if generated:
                    raise BuildRefusal(
                        f"The held-out split {name!r} contains generated rows. A held-out split "
                        "may hold only rows from sources; move the generation step after the "
                        "split, or keep generated rows out of it.",
                        code="held_out_contains_generated",
                        details={"split": name, "sample_keys": generated},
                    )
            splits.append(
                {
                    "name": name,
                    "held_out": is_held_out,
                    "rows": pq.ParquetFile(path).metadata.num_rows,
                    "bytes": path.stat().st_size,
                    "file_sha256": file_sha256(path),
                    "logical_digest": logical_digest(path),
                    "path": f"versions/{version_id}/{path.name}",
                }
            )
        warnings: list[dict[str, Any]] = []
        warning = duplicate_metadata_warning([p for _, p in written], roles)
        if warning is not None:
            warnings.append(warning)

        dataset = session.execute(
            select(Dataset).where(Dataset.id == build.dataset_id).with_for_update()
        ).scalar_one()
        number = dataset.next_version_number
        inputs = list(request["inputs"])
        parent = next((i["version_id"] for i in inputs if i["kind"] == InputKind.VERSION), None)
        origin = held_out_origin(session, steps, held_out, version_id, parent)
        step_records = [
            {
                "index": s.index,
                "kind": s.execution.kind,
                "operator": s.execution.operator_name,
                "version": s.execution.operator_version,
                "manifest_hash": s.execution.manifest_hash,
                "execution_id": s.execution.id,
                "identity": s.execution.identity_digest,
                "reused": s.reused,
                "step_seed": s.execution.step_seed,
            }
            for s in steps
        ]
        summary = drop_summary(steps)
        document = build_manifest(
            version_id=version_id,
            dataset={"id": dataset.id, "name": dataset.name, "target_type": dataset.target_type},
            number=number,
            inputs=inputs,
            parent_version_id=parent,
            recipe={
                "hash": request["recipe_hash"],
                "revision_id": request["recipe_revision_id"],
                "body": recipe_body,
            },
            seed=int(request["seed"]),
            bindings=request["bindings"],
            rowkey_scheme=request["rowkey_scheme"],
            column_roles=roles,
            splits=splits,
            held_out_origin_version_id=origin,
            steps=step_records,
            drop_summary=summary,
            sources=transitive_sources(session, inputs),
            warnings=warnings,
            build_job_id=job.id,
            created_by=job.started_by,
            created_by_origin=job.started_by_origin,
        )
        content = manifest_bytes(document)
        manifest_path = staged / "manifest.json"
        manifest_path.write_bytes(content)
        _fsync(manifest_path)
        _fsync(staged)
        destination = version_dir(version_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.rename(staged, destination)  # BEFORE the commit (FR-002.35)
    except BaseException:
        shutil.rmtree(staged, ignore_errors=True)
        session.rollback()
        raise

    version = Version(
        id=version_id,
        dataset_id=dataset.id,
        number=number,
        state=VersionState.COMPLETED,
        parent_version_id=parent,
        request_digest=build.request_digest,
        inputs=inputs,
        recipe_hash=request["recipe_hash"],
        recipe_revision_id=request["recipe_revision_id"],
        seed=int(request["seed"]),
        bindings=list(request["bindings"]),
        rowkey_scheme=request["rowkey_scheme"],
        column_roles=roles,
        splits=splits,
        total_rows=document["total_rows"],
        total_bytes=document["total_bytes"],
        held_out_origin_version_id=origin,
        warnings=warnings,
        drop_summary=summary,
        manifest=content,
        manifest_sha256=bytes_sha256(content),
        build_job_id=job.id,
        created_by=job.started_by,
        created_by_origin=job.started_by_origin,
    )
    session.add(version)
    session.flush()
    for position, item in enumerate(inputs):
        session.add(
            VersionInput(
                version_id=version_id,
                position=position,
                kind=item["kind"],
                source_id=item.get("source_id"),
                input_version_id=item.get("version_id"),
            )
        )
    for linked in steps:
        session.add(
            VersionStep(
                version_id=version_id,
                step_index=linked.index,
                step_execution_id=linked.execution.id,
                reused=linked.reused,
            )
        )
    dataset.next_version_number = number + 1
    build.version_id = version_id
    session.commit()  # AFTER the rename; a failure here leaves an orphan for the sweeper

    try:
        send_task(POST_VERSION_TASK, args=[version_id])
    except Exception as exc:  # noqa: BLE001 - the audit sweeper (004) is the backstop
        logger.warning("post_version_enqueue_failed for version %s: %s", version_id, exc)
    return version_id
