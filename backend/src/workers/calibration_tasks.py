"""``midataworks.calibration.compute_record`` on the ``default`` queue (FR-006.21; FTDD 006 §2.2).

The worker imports ``record_service`` only. A cancelled job (``OperatorCancelled``, a
``BaseException``) or a failed one writes no record: the record, its checks and its verdict are
written in one transaction at the very end of ``record_service.compute``.
"""

from __future__ import annotations

import logging
from typing import Any

from ..core.cancellation import cooperative_cancel, record_progress
from ..core.celery_app import celery_app
from ..core.database import get_sync_db
from ..core.job_kinds import get_job_kind
from ..services.calibration import record_service
from ..services.job_service import claim_job, dispatch_queued
from .emit import emit

logger = logging.getLogger(__name__)

TASK_NAME = "midataworks.calibration.compute_record"


def _next_jobs() -> None:
    try:
        with get_sync_db() as db:
            dispatch_queued(db)
    except Exception as exc:  # noqa: BLE001 - Beat dispatches again
        logger.warning("Could not dispatch the next queued job: %s", exc)


@celery_app.task(name=TASK_NAME, acks_late=True)
def compute_record(job_id: str) -> dict[str, Any]:
    try:
        return run_job(job_id)
    finally:
        _next_jobs()


@cooperative_cancel
def run_job(job_id: str) -> dict[str, Any]:
    with get_sync_db() as db:
        job = claim_job(db, job_id)
        if job is None:
            return {"status": "skipped", "job_id": job_id}
        room = get_job_kind(job.kind).room(job_id)
        emit(room, "job:status", {"job_id": job_id, "status": "running"})
        try:
            record = record_service.compute(db, job)
        except Exception as exc:
            db.rollback()
            logger.exception("calibration.record.failed job=%s", job_id)
            record_progress(job_id, status="failed", error=f"{type(exc).__name__}: {exc}"[:4000])
            emit(room, "job:status", {"job_id": job_id, "status": "failed"})
            return {"status": "failed", "job_id": job_id}
        record_id = record.id
    record_progress(
        job_id,
        status="completed",
        progress=100.0,
        message="Calibration recorded.",
        result={"record_id": record_id},
    )
    emit(room, "calibration:completed", {"job_id": job_id, "record_id": record_id})
    emit(room, "job:status", {"job_id": job_id, "status": "completed", "progress": 100.0})
    return {"status": "completed", "job_id": job_id, "record_id": record_id}
