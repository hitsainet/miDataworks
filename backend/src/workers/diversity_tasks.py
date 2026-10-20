"""Celery tasks for feature 007's diversity reports (FTDD 007 section 6.6; FTASKS 9.1).

- ``midataworks.generation.diversity_report`` (job kind ``diversity_report``, ``labeling`` queue:
  it calls the embeddings endpoint);
- ``midataworks.generation.diversity_sweep`` (Beat, ``default`` queue): queues a report for each
  completed version with generated rows and none for its default column, once (idempotent).
"""

from __future__ import annotations

import logging
from typing import Any

from ..core.cancellation import CancelCheck, cooperative_cancel, record_progress
from ..core.celery_app import celery_app
from ..core.database import get_sync_db
from ..core.errors import AppError
from ..services.generation import diversity_service
from ..services.job_service import claim_job, dispatch_queued

logger = logging.getLogger(__name__)

REPORT_TASK = "midataworks.generation.diversity_report"
SWEEP_TASK = "midataworks.generation.diversity_sweep"
SYSTEM = "midataworks"


@cooperative_cancel
def report_job(job_id: str) -> dict[str, Any]:
    with get_sync_db() as session:
        job = claim_job(session, job_id)
        if job is None:
            return {"job_id": job_id, "outcome": "skipped"}
        params = dict(job.params)
        CancelCheck(job_id).raise_if_cancelled("Stopped before the report started.")
        try:
            row = diversity_service.compute(
                session,
                str(params["version_id"]),
                str(params["column"]),
                job_id=job_id,
                who=job.started_by,
                origin=job.started_by_origin,
            )
        except AppError as exc:
            record_progress(
                job_id,
                status="failed",
                error=exc.message,
                result={
                    "error": {"code": exc.code, "message": exc.message, "details": exc.details}
                },
            )
            return {"job_id": job_id, "outcome": "failed"}
        except Exception as exc:  # noqa: BLE001 - a report failure is reported, never raised
            logger.exception("diversity report %s failed", job_id)
            record_progress(
                job_id,
                status="failed",
                error=f"The diversity report failed ({type(exc).__name__}).",
                result={"error": {"code": "INTERNAL_ERROR"}},
            )
            return {"job_id": job_id, "outcome": "failed"}
        record_progress(
            job_id,
            status="completed",
            progress=100.0,
            result={"report_id": row.id, "verdict": row.verdict, "version_id": row.version_id},
        )
        return {"job_id": job_id, "outcome": "completed", "report_id": row.id}


@celery_app.task(name=REPORT_TASK, acks_late=True)
def diversity_report(job_id: str) -> dict[str, Any]:
    try:
        return report_job(job_id)
    finally:
        try:
            with get_sync_db() as db:
                dispatch_queued(db)
        except Exception as exc:  # noqa: BLE001 - Beat dispatches again
            logger.warning("Could not dispatch the next queued job: %s", exc)


def sweep() -> list[str]:
    """Queue the missing reports; returns the job ids created."""
    created: list[str] = []
    with get_sync_db() as session:
        for version_id, column in diversity_service.versions_needing_reports(session):
            created.append(
                diversity_service.create_job(session, version_id, column, SYSTEM, "operator")
            )
        if created:
            dispatch_queued(session)
    if created:
        logger.info("diversity_sweep queued=%d", len(created))
    return created


@celery_app.task(name=SWEEP_TASK, acks_late=True)
def diversity_sweep() -> dict[str, Any]:
    return {"queued": sweep()}
