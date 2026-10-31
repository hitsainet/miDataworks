"""The walking-skeleton job (Foundation task 5.8).

It exercises every piece a real job uses: a claimed ``dw_jobs`` row, a Parquet file written
through the stage-then-rename writer and read back with DuckDB in-process, time-throttled
progress with a database heartbeat, Socket.IO emits through the internal route, cooperative
cancellation that keeps what was written, and dispatch of the next queued job when it ends.
With ``check_hf_token`` it also resolves the stored Hugging Face token in the worker, which is
what the secret-never-written test drives.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from ..core.cancellation import CancelCheck, cooperative_cancel, record_progress
from ..core.celery_app import celery_app
from ..core.database import get_sync_db
from ..core.job_kinds import get_job_kind
from ..core.storage import run_dir, staged_path
from ..services.job_service import claim_job, dispatch_queued
from .emit import emit
from .secrets import resolve_hf_token

logger = logging.getLogger(__name__)


def write_and_verify_parquet(job_id: str, rows: int) -> dict[str, Any]:
    """Write ``rows`` rows through the staged writer, read them back with DuckDB, compare."""
    texts = [f"row {i} of the self-test" for i in range(rows)]
    table = pa.table({"row_id": list(range(rows)), "text": texts})
    destination = run_dir(job_id) / "selftest.parquet"
    with staged_path(destination) as staged:
        pq.write_table(table, staged)
    expected = hashlib.sha256("\n".join(texts).encode("utf-8")).hexdigest()
    con = duckdb.connect()
    try:
        count, ids = con.execute(
            "SELECT count(*), sum(row_id) FROM read_parquet(?)", [str(destination)]
        ).fetchone() or (0, 0)
        joined = con.execute(
            "SELECT string_agg(text, chr(10) ORDER BY row_id) FROM read_parquet(?)",
            [str(destination)],
        ).fetchone()
    finally:
        con.close()
    actual = hashlib.sha256(str(joined[0] if joined else "").encode("utf-8")).hexdigest()
    if count != rows or actual != expected or ids != rows * (rows - 1) // 2:
        raise RuntimeError("DuckDB read back different rows from those written")
    return {"path": str(destination), "rows": int(count), "text_sha256": actual}


def _next_jobs() -> None:
    try:
        with get_sync_db() as db:
            dispatch_queued(db)
    except Exception as exc:  # noqa: BLE001 - Beat dispatches again
        logger.warning("Could not dispatch the next queued job: %s", exc)


@celery_app.task(name="midataworks.selftest.run", acks_late=True)
def run_selftest(job_id: str) -> dict[str, Any]:
    try:
        return _run(job_id)
    finally:
        _next_jobs()


@cooperative_cancel
def _run(job_id: str) -> dict[str, Any]:
    with get_sync_db() as db:
        job = claim_job(db, job_id)
        if job is None:
            logger.info("Self-test %s was not claimable (cancelled before it started)", job_id)
            return {"status": "skipped", "job_id": job_id}
        params = dict(job.params)
        room = get_job_kind(job.kind).room(job_id)
        hf_token_configured: bool | None = None
        if params.get("check_hf_token"):
            hf_token_configured = resolve_hf_token(db) is not None

    emit(room, "job:status", {"job_id": job_id, "status": "running"})
    written = write_and_verify_parquet(job_id, int(params.get("rows", 1000)))
    kept = {"parquet": written, "hf_token_configured": hf_token_configured}

    duration = float(params.get("duration_seconds", 5.0))
    step = float(params.get("step_seconds", 0.5))
    checker = CancelCheck(job_id, min_interval_s=min(step, 2.0))
    started = time.monotonic()
    while True:
        elapsed = time.monotonic() - started
        if elapsed >= duration:
            break
        checker.raise_if_cancelled(result=kept)
        time.sleep(min(step, max(duration - elapsed, 0.0)))
        percent = min(100.0, 100.0 * (time.monotonic() - started) / duration) if duration else 100.0
        record_progress(job_id, progress=percent, message=f"Self-test {percent:.0f}% done")
        emit(room, "job:progress", {"job_id": job_id, "progress": percent})

    checker.raise_if_cancelled(result=kept)
    record_progress(
        job_id, status="completed", progress=100.0, message="Self-test finished.", result=kept
    )
    emit(room, "job:status", {"job_id": job_id, "status": "completed", "progress": 100.0})
    return {"status": "completed", "job_id": job_id, **kept}
