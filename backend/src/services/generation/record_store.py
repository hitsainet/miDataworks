"""Stage → rename → idempotent commit of generation chunks (FTDD 007 section 4.2; ADR-004, ADR-007).

A chunk is written under ``staging/``, renamed into ``runs/<run_id>/gen/<stage>-chunk-NNNNNN.parquet``
and committed in ONE transaction whose first statement inserts the chunk marker
(``dw_generation_chunks``) with ``ON CONFLICT DO NOTHING``: no row back means the chunk was already
committed and nothing else is written. So a crash before the rename regenerates that chunk only; a
crash between rename and commit leaves a renamed file that :func:`recover_uncommitted` commits FROM
THE FILE, with no endpoint call; a commit is never applied twice.

The Parquet file holds every record field AND the text (prompt and response); the database rows hold
everything but the text (ADR-003, R-03.10). Pairs are derived from the records — a pair exists
exactly when both sides of a prompt were ``generated`` — so a recovered chunk yields the same pairs.

Chunk files live under the RUN's directory, not the job's (as 005 decided for label chunks): a run
has several jobs across resumes, and recovery must see every chunk of the run.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import insert, select, text
from sqlalchemy.orm import Session

from ...core.clock import utc_now
from ...core.storage import data_dir, resolve_under_data_dir, run_dir, staged_path
from ...models.generation import GenerationChunk, GenerationPair, GenerationRecord
from ..identity import file_sha256

CHUNK_FILE = re.compile(r"^(expand|respond)-chunk-(\d{6})\.parquet$")
SEEDS_FILE = "seeds.parquet"

CHUNK_SCHEMA = pa.schema(
    [
        ("record_index", pa.int64()),
        ("stage", pa.string()),
        ("seed_position", pa.int32()),
        ("row_key", pa.string()),
        ("seed_row_key", pa.string()),
        ("prompt_row_key", pa.string()),
        ("response_index", pa.int32()),
        ("side", pa.string()),
        ("template_id", pa.string()),
        ("model_id", pa.string()),
        ("model_revision", pa.string()),
        ("requested_set_hash", pa.string()),
        ("reported_steering", pa.string()),
        ("steering_check", pa.string()),
        ("check_reasons", pa.list_(pa.string())),
        ("seed_sent", pa.int64()),
        ("seed_confirmed", pa.bool_()),
        ("latency_ms", pa.int64()),
        ("finish_reason", pa.string()),
        ("outcome", pa.string()),
        ("reason_code", pa.string()),
        ("prompt", pa.string()),
        ("text", pa.string()),
    ]
)


@dataclass(frozen=True)
class GenRecord:
    record_index: int
    stage: str
    seed_position: int
    row_key: str | None
    seed_row_key: str
    prompt_row_key: str
    response_index: int
    side: str | None
    template_id: str | None
    model_id: str | None
    model_revision: str | None
    requested_set_hash: str | None
    reported_steering: str | None
    steering_check: str
    check_reasons: tuple[str, ...]
    seed_sent: int | None
    seed_confirmed: bool | None
    latency_ms: int | None
    finish_reason: str | None
    outcome: str
    reason_code: str | None
    prompt: str | None
    text: str | None


def gen_dir(run_id: str) -> Path:
    return run_dir(run_id) / "gen"


def chunk_path(run_id: str, stage: str, chunk_index: int) -> Path:
    return gen_dir(run_id) / f"{stage}-chunk-{chunk_index:06d}.parquet"


def seeds_path(run_id: str) -> Path:
    return gen_dir(run_id) / SEEDS_FILE


def _relative(path: Path) -> str:
    return str(resolve_under_data_dir(path).relative_to(data_dir().resolve()))


def _table(records: Sequence[GenRecord]) -> pa.Table:
    columns: dict[str, list[Any]] = {name: [] for name in CHUNK_SCHEMA.names}
    for record in records:
        row = asdict(record)
        row["check_reasons"] = list(record.check_reasons)
        for name in CHUNK_SCHEMA.names:
            columns[name].append(row[name])
    return pa.table(columns, schema=CHUNK_SCHEMA)


def read_chunk(path: Path) -> list[GenRecord]:
    rows = pq.read_table(path, schema=CHUNK_SCHEMA).to_pylist()
    out = []
    for row in rows:
        row["check_reasons"] = tuple(row["check_reasons"] or ())
        out.append(GenRecord(**row))
    return out


def stage_chunk(
    run_id: str,
    stage: str,
    chunk_index: int,
    records: Sequence[GenRecord],
    *,
    after_rename: Callable[[Path], None] | None = None,
) -> tuple[Path, str]:
    destination = chunk_path(run_id, stage, chunk_index)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with staged_path(destination) as staged:
        pq.write_table(_table(records), staged)
    if after_rename is not None:
        after_rename(destination)  # the crash test's pause point
    return destination, file_sha256(destination)


def derive_pairs(records: Sequence[GenRecord], chosen_side: str | None) -> list[dict[str, Any]]:
    """One pair per prompt whose side A AND side B were both ``generated`` (FR-007.19)."""
    if chosen_side is None:
        return []
    by_prompt: dict[tuple[str, int], dict[str, GenRecord]] = {}
    for record in records:
        if record.stage != "respond" or record.side not in ("a", "b"):
            continue
        by_prompt.setdefault((record.prompt_row_key, record.response_index), {})[
            record.side
        ] = record
    pairs = []
    for (prompt_key, response_index), sides in sorted(by_prompt.items()):
        a, b = sides.get("a"), sides.get("b")
        if a is None or b is None:
            continue
        if a.outcome != "generated" or b.outcome != "generated":
            continue
        if a.steering_check != "match" or b.steering_check != "match":
            continue
        pairs.append(
            {
                "prompt_row_key": prompt_key,
                "pair_index": response_index,
                "record_index_a": a.record_index,
                "record_index_b": b.record_index,
                "chosen_side": chosen_side,
                "shared_seed": int(a.seed_sent or 0),
            }
        )
    return pairs


def _record_values(run_id: str, chunk_index: int, record: GenRecord) -> dict[str, Any]:
    values = asdict(record)
    values.pop("prompt")
    values.pop("text")
    values["check_reasons"] = list(record.check_reasons)
    values["run_id"] = run_id
    values["chunk_index"] = chunk_index
    return values


def commit_chunk(
    session: Session,
    run_id: str,
    stage: str,
    chunk_index: int,
    job_id: str,
    path: Path,
    sha256: str,
    records: Sequence[GenRecord] | None,
    chosen_side: str | None,
) -> bool:
    """One transaction: the marker first (``ON CONFLICT DO NOTHING``), then records and pairs.
    Returns False when the chunk was already committed (nothing is written)."""
    rows = list(records) if records is not None else read_chunk(path)
    marker = session.execute(
        text(
            "INSERT INTO dw_generation_chunks "
            "(run_id, stage, chunk_index, job_id, row_count, path, file_sha256, committed_at) "
            "VALUES (:run, :stage, :idx, :job, :n, :path, :sha, :at) "
            "ON CONFLICT DO NOTHING RETURNING chunk_index"
        ),
        {
            "run": run_id,
            "stage": stage,
            "idx": chunk_index,
            "job": job_id,
            "n": len(rows),
            "path": _relative(path),
            "sha": sha256,
            "at": utc_now(),
        },
    ).scalar_one_or_none()
    if marker is None:
        session.rollback()
        return False
    if rows:
        session.execute(
            insert(GenerationRecord), [_record_values(run_id, chunk_index, r) for r in rows]
        )
    pairs = derive_pairs(rows, chosen_side)
    if pairs:
        session.execute(
            insert(GenerationPair),
            [{**p, "run_id": run_id, "chunk_index": chunk_index} for p in pairs],
        )
    session.commit()
    return True


def committed(session: Session, run_id: str) -> set[tuple[str, int]]:
    return {
        (str(stage), int(idx))
        for stage, idx in session.execute(
            select(GenerationChunk.stage, GenerationChunk.chunk_index).where(
                GenerationChunk.run_id == run_id
            )
        ).all()
    }


def chunk_files(run_id: str) -> dict[tuple[str, int], Path]:
    directory = gen_dir(run_id)
    if not directory.is_dir():
        return {}
    found: dict[tuple[str, int], Path] = {}
    for entry in directory.iterdir():
        match = CHUNK_FILE.match(entry.name)
        if match:
            found[(match.group(1), int(match.group(2)))] = entry
    return found


def recover_uncommitted(
    session: Session, run_id: str, job_id: str, chosen_side: str | None
) -> list[tuple[str, int]]:
    """Commit every renamed chunk file that has no marker, FROM THE FILE (no regeneration)."""
    done = committed(session, run_id)
    recovered = []
    for (stage, index), path in sorted(chunk_files(run_id).items()):
        if (stage, index) in done:
            continue
        if commit_chunk(
            session, run_id, stage, index, job_id, path, file_sha256(path), None, chosen_side
        ):
            recovered.append((stage, index))
    return recovered


def next_chunk_index(session: Session, run_id: str, stage: str) -> int:
    known = {i for s, i in committed(session, run_id) if s == stage} | {
        i for (s, i) in chunk_files(run_id) if s == stage
    }
    return max(known) + 1 if known else 0


def done_positions(session: Session, run_id: str, stage: str) -> set[int]:
    """Seed positions with committed records in ``stage``: a prompt unit is atomic in a chunk."""
    return {
        int(p)
        for p in session.execute(
            select(GenerationRecord.seed_position)
            .where(GenerationRecord.run_id == run_id, GenerationRecord.stage == stage)
            .distinct()
        ).scalars()
    }


def committed_chunk_paths(session: Session, run_id: str, stage: str) -> list[Path]:
    """The COMMITTED chunk files of one stage, in chunk order (what versions may read)."""
    rows = session.execute(
        select(GenerationChunk.path)
        .where(GenerationChunk.run_id == run_id, GenerationChunk.stage == stage)
        .order_by(GenerationChunk.chunk_index)
    ).scalars()
    return [resolve_under_data_dir(str(p)) for p in rows]


def iter_committed(paths: Sequence[Path]) -> Iterator[GenRecord]:
    for path in paths:
        yield from read_chunk(path)


def texts_for(
    session: Session, run_id: str, wanted: set[tuple[str, int]]
) -> dict[tuple[str, int], tuple[str | None, str | None]]:
    """``(stage, record_index) -> (prompt, text)`` read from the committed chunk Parquet."""
    out: dict[tuple[str, int], tuple[str | None, str | None]] = {}
    if not wanted:
        return out
    stages = {s for s, _ in wanted}
    for stage in stages:
        for record in iter_committed(committed_chunk_paths(session, run_id, stage)):
            key = (record.stage, record.record_index)
            if key in wanted:
                out[key] = (record.prompt, record.text)
    return out


def write_seeds(run_id: str, table: pa.Table) -> Path:
    destination = seeds_path(run_id)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with staged_path(destination) as staged:
        pq.write_table(table, staged)
    return destination


def read_seeds(run_id: str) -> pa.Table | None:
    path = seeds_path(run_id)
    return pq.read_table(path) if path.is_file() else None
