"""Stage → rename → idempotent commit of label chunks (FR-005.32; ADR-004, ADR-007; FTID 005 4).

A chunk is written under ``staging/``, renamed into ``runs/<label_run_id>/chunk-NNNNNN.parquet``
and committed to ``dw_labels`` in ONE transaction whose first statement inserts the chunk's commit
marker (``dw_label_run_chunks``) with ``ON CONFLICT DO NOTHING``: no row back means the chunk is
already committed and nothing else is written. So:

- a crash before the rename leaves nothing; resume rescores that chunk only;
- a crash between rename and commit leaves a renamed file with no marker; recovery commits it
  from the file, with no endpoint call (:func:`recover_uncommitted`);
- a commit is never applied twice.

Chunk files live under the RUN's directory (not the job's, as FTDD 005 section 2.2 first said): a
run has several jobs across resumes, and recovery must see every chunk of the run.

Reused labels (FR-005.29) are copied in the database only (``INSERT … SELECT``), and every
completed run also publishes ``labels.parquet`` (all its labels) for readers that work from files —
the Threshold labeler operator, which may not open a database session (003 ``RunContext``).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import insert, select, text
from sqlalchemy.orm import Session

from ..core.clock import utc_now
from ..core.storage import data_dir, resolve_under_data_dir, run_dir, staged_path
from ..models.label import Label
from ..models.label_run import LabelRun, LabelRunChunk
from .identity import file_sha256

CHUNK_FILE = re.compile(r"^chunk-(\d{6})\.parquet$")
LABELS_FILE = "labels.parquet"

CHUNK_SCHEMA = pa.schema(
    [
        ("row_key", pa.string()),
        ("outcome", pa.string()),
        ("parsed_value", pa.string()),
        ("probability", pa.float64()),
        ("distribution", pa.string()),
        ("raw_output", pa.string()),
        ("rationale", pa.string()),
        ("steering_state", pa.string()),
        ("latency_ms", pa.int64()),
        ("skip_reason", pa.string()),
        ("provisional", pa.bool_()),
        ("reused_from_run_id", pa.string()),
        ("scored_at", pa.timestamp("us", tz="UTC")),
    ]
)


@dataclass(frozen=True)
class LabelRecord:
    row_key: str
    outcome: str
    parsed_value: Any
    probability: float | None
    distribution: dict[str, float] | None
    raw_output: Any
    rationale: str | None
    steering_state: str
    latency_ms: int | None
    skip_reason: str | None
    scored_at: datetime
    provisional: bool = False
    reused_from_run_id: str | None = None


def _dumps(value: Any) -> str | None:
    return None if value is None else json.dumps(value, sort_keys=True, ensure_ascii=False)


def _loads(value: str | None) -> Any:
    return None if value is None else json.loads(value)


def chunk_path(label_run_id: str, chunk_index: int) -> Path:
    return run_dir(label_run_id) / f"chunk-{chunk_index:06d}.parquet"


def labels_path(label_run_id: str) -> Path:
    return run_dir(label_run_id) / LABELS_FILE


def _relative(path: Path) -> str:
    return str(resolve_under_data_dir(path).relative_to(data_dir().resolve()))


def _sha256(path: Path) -> str:
    return file_sha256(path)


def _table(records: Sequence[LabelRecord]) -> pa.Table:
    return pa.table(
        {
            "row_key": [r.row_key for r in records],
            "outcome": [r.outcome for r in records],
            "parsed_value": [_dumps(r.parsed_value) for r in records],
            "probability": [r.probability for r in records],
            "distribution": [_dumps(r.distribution) for r in records],
            "raw_output": [_dumps(r.raw_output) for r in records],
            "rationale": [r.rationale for r in records],
            "steering_state": [r.steering_state for r in records],
            "latency_ms": [r.latency_ms for r in records],
            "skip_reason": [r.skip_reason for r in records],
            "provisional": [r.provisional for r in records],
            "reused_from_run_id": [r.reused_from_run_id for r in records],
            "scored_at": [r.scored_at for r in records],
        },
        schema=CHUNK_SCHEMA,
    )


def read_chunk(path: Path) -> list[LabelRecord]:
    table = pq.read_table(path, schema=CHUNK_SCHEMA)
    rows = table.to_pylist()
    return [
        LabelRecord(
            row_key=r["row_key"],
            outcome=r["outcome"],
            parsed_value=_loads(r["parsed_value"]),
            probability=r["probability"],
            distribution=_loads(r["distribution"]),
            raw_output=_loads(r["raw_output"]),
            rationale=r["rationale"],
            steering_state=r["steering_state"],
            latency_ms=r["latency_ms"],
            skip_reason=r["skip_reason"],
            scored_at=r["scored_at"],
            provisional=bool(r["provisional"]),
            reused_from_run_id=r["reused_from_run_id"],
        )
        for r in rows
    ]


def stage_chunk(
    label_run_id: str,
    chunk_index: int,
    records: Sequence[LabelRecord],
    *,
    after_rename: Callable[[Path], None] | None = None,
) -> tuple[Path, str]:
    """Write the chunk under ``staging/`` and rename it into the run directory."""
    destination = chunk_path(label_run_id, chunk_index)
    with staged_path(destination) as staged:
        pq.write_table(_table(records), staged)
    if after_rename is not None:
        after_rename(destination)  # the crash test's pause point
    return destination, _sha256(destination)


def _label_values(
    label_run_id: str, fingerprint: str, chunk_index: int | None, record: LabelRecord
) -> dict[str, Any]:
    return {
        "label_run_id": label_run_id,
        "row_key": record.row_key,
        "labeler_fingerprint": fingerprint,
        "outcome": record.outcome,
        "parsed_value": record.parsed_value,
        "probability": record.probability,
        "distribution": record.distribution,
        "raw_output": record.raw_output,
        "rationale": record.rationale,
        "steering_state": record.steering_state,
        "latency_ms": record.latency_ms,
        "skip_reason": record.skip_reason,
        "provisional": record.provisional,
        "reused_from_run_id": record.reused_from_run_id,
        "chunk_index": chunk_index,
        "scored_at": record.scored_at,
    }


def commit_chunk(
    session: Session,
    run: LabelRun,
    chunk_index: int,
    job_id: str,
    path: Path,
    sha256: str,
    records: Sequence[LabelRecord] | None = None,
) -> bool:
    """One transaction: the marker first (``ON CONFLICT DO NOTHING``), then the labels.

    Returns False when the chunk was already committed (nothing is written)."""
    marker = session.execute(
        text(
            "INSERT INTO dw_label_run_chunks "
            "(label_run_id, chunk_index, job_id, row_count, file_path, file_sha256, committed_at) "
            "VALUES (:run, :idx, :job, :n, :path, :sha, :at) "
            "ON CONFLICT DO NOTHING RETURNING chunk_index"
        ),
        {
            "run": run.id,
            "idx": chunk_index,
            "job": job_id,
            "n": len(records) if records is not None else 0,
            "path": _relative(path),
            "sha": sha256,
            "at": utc_now(),
        },
    ).scalar_one_or_none()
    if marker is None:
        session.rollback()
        return False
    rows = list(records) if records is not None else read_chunk(path)
    if records is None:
        session.execute(
            text(
                "UPDATE dw_label_run_chunks SET row_count = :n "
                "WHERE label_run_id = :run AND chunk_index = :idx"
            ),
            {"n": len(rows), "run": run.id, "idx": chunk_index},
        )
    if rows:
        session.execute(
            insert(Label),
            [_label_values(run.id, run.labeler_fingerprint, chunk_index, r) for r in rows],
        )
    session.commit()
    return True


def committed_indexes(session: Session, label_run_id: str) -> set[int]:
    return set(
        session.execute(
            select(LabelRunChunk.chunk_index).where(LabelRunChunk.label_run_id == label_run_id)
        ).scalars()
    )


def chunk_files(label_run_id: str) -> dict[int, Path]:
    directory = run_dir(label_run_id)
    if not directory.is_dir():
        return {}
    found: dict[int, Path] = {}
    for entry in directory.iterdir():
        match = CHUNK_FILE.match(entry.name)
        if match:
            found[int(match.group(1))] = entry
    return found


def recover_uncommitted(session: Session, run: LabelRun, job_id: str) -> list[int]:
    """Commit every renamed chunk file that has no marker, from the file (no rescoring)."""
    committed = committed_indexes(session, run.id)
    recovered = []
    for index, path in sorted(chunk_files(run.id).items()):
        if index in committed:
            continue
        if commit_chunk(session, run, index, job_id, path, _sha256(path)):
            recovered.append(index)
    return recovered


def next_chunk_index(session: Session, label_run_id: str) -> int:
    known = committed_indexes(session, label_run_id) | set(chunk_files(label_run_id))
    return max(known) + 1 if known else 0


def recorded_keys(session: Session, label_run_id: str) -> pa.Table:
    keys = session.execute(
        select(Label.row_key).where(Label.label_run_id == label_run_id)
    ).scalars()
    return pa.table({"row_key": pa.array(list(keys), pa.string())})


def insert_records(
    session: Session, run: LabelRun, records: Sequence[LabelRecord], chunk_index: int | None
) -> int:
    """Insert labels with no chunk file (reuse, re-derive, aggregate). Existing keys are kept."""
    if not records:
        return 0
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    statement = pg_insert(Label).on_conflict_do_nothing(index_elements=["label_run_id", "row_key"])
    result = session.execute(
        statement.returning(Label.row_key),
        [_label_values(run.id, run.labeler_fingerprint, chunk_index, r) for r in records],
    )
    return len(result.scalars().all())


def export_labels(session: Session, run: LabelRun) -> Path:
    """Publish every label of the run as ``labels.parquet`` (stage then rename)."""
    rows = session.execute(
        select(Label).where(Label.label_run_id == run.id).order_by(Label.row_key)
    ).scalars()
    records = [
        LabelRecord(
            row_key=r.row_key,
            outcome=r.outcome,
            parsed_value=r.parsed_value,
            probability=r.probability,
            distribution=r.distribution,
            raw_output=r.raw_output,
            rationale=r.rationale,
            steering_state=r.steering_state,
            latency_ms=r.latency_ms,
            skip_reason=r.skip_reason,
            scored_at=r.scored_at,
            provisional=r.provisional,
            reused_from_run_id=r.reused_from_run_id,
        )
        for r in rows
    ]
    destination = labels_path(run.id)
    with staged_path(destination) as staged:
        pq.write_table(_table(records), staged)
    export_labeler(run)
    return destination


LABELER_FILE = "labeler.json"


def labeler_path(label_run_id: str) -> Path:
    return run_dir(label_run_id) / LABELER_FILE


def export_labeler(run: LabelRun) -> Path:
    """``runs/<id>/labeler.json``: WHO labeled, beside the labels, for readers that work from
    files (009's minimal-pair join records the judge's identity; an operator opens no database
    session). Written with the labels, so a published run always has both."""
    from ..core.canonical_json import canonical_json
    from ..core.storage import atomic_write_bytes

    document = {
        "label_run_id": run.id,
        "kind": run.kind,
        "input_version_id": str(run.input_version_id),
        "rubric_id": run.rubric_id,
        "template_id": run.template_id,
        "labeler_identity": dict(run.labeler_identity),
        "labeler_identity_hash": run.labeler_identity_hash,
        "labeler_fingerprint": run.labeler_fingerprint,
    }
    destination = labeler_path(run.id)
    destination.parent.mkdir(parents=True, exist_ok=True)
    return atomic_write_bytes(destination, canonical_json(document))
