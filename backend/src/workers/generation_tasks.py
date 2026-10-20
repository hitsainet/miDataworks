"""Celery task for feature 007's generation runs (FTDD 007 section 6.3; FTASKS 6.1).

``midataworks.generation.run`` (job kind ``generation_run``) runs on the ``labeling`` queue
(ADR-006: classifier, judge and generation calls). Importing this module in a worker also installs
feature 005 into 003's endpoint port and feature 007 into 002's and 006's registries.
"""

from __future__ import annotations

import logging
from typing import Any

from celery.signals import worker_process_init
from sqlalchemy.orm import Session

from ..core.cancellation import cooperative_cancel, record_progress
from ..core.celery_app import celery_app
from ..core.database import get_sync_db
from ..models.generation import GenerationRun, GenerationRunJob
from ..services.generation.run_engine import GenerationEngine
from ..services.generation.run_service import end_after_worker_lost, orphaned_runs, set_state
from ..services.job_service import claim_job, dispatch_queued
from ..services.model_lease_holder import HOLDER
from . import janitor

logger = logging.getLogger(__name__)

TASK_NAME = "midataworks.generation.run"


def _engine() -> GenerationEngine:
    """The engine a task uses (tests replace this to inject fakes)."""
    return GenerationEngine()


def _next_jobs() -> None:
    try:
        with get_sync_db() as db:
            dispatch_queued(db)
    except Exception as exc:  # noqa: BLE001 - Beat dispatches again
        logger.warning("Could not dispatch the next queued job: %s", exc)


@cooperative_cancel
def run_job(job_id: str) -> dict[str, Any]:
    with get_sync_db() as session:
        job = claim_job(session, job_id)
        if job is None:
            return {"job_id": job_id, "outcome": "skipped"}
        try:
            return _engine().run(session, job)
        except Exception as exc:
            logger.exception("generation job %s failed unexpectedly", job_id)
            session.rollback()
            link = (
                session.query(GenerationRunJob)
                .filter(GenerationRunJob.job_id == job_id)
                .one_or_none()
            )
            if link is not None:
                run = session.get(GenerationRun, link.run_id, populate_existing=True)
                if run is not None:
                    set_state(
                        session,
                        run,
                        "failed",
                        error={"code": "INTERNAL_ERROR", "message": type(exc).__name__},
                    )
            record_progress(
                job_id,
                status="failed",
                error="The generation job failed unexpectedly; the log carries the details.",
                result={"error": {"code": "INTERNAL_ERROR"}},
            )
            return {"job_id": job_id, "outcome": "failed"}


@celery_app.task(name=TASK_NAME, acks_late=True)
def run_generation(job_id: str) -> dict[str, Any]:
    try:
        return run_job(job_id)
    finally:
        _next_jobs()


# --- the janitor (2026-10-18: the 005 FTASKS 9.5 defect, audited in 007) ----------------------


def _lease_caller(base_url: str) -> Any:
    from ..services.label_run_service import caller_for

    return caller_for(base_url, None)


def recover_run(db: Session, job_id: str, status: str, reason: str) -> str | None:
    """Move the run of a job that ended without its worker to a resumable state, and leave the
    miLLM lease the job still holds (the last member releases it at once, rather than at Beat's
    next ``renew_all`` or the lease's TTL)."""
    run_id = end_after_worker_lost(db, job_id, status, reason)
    try:
        HOLDER.leave_for(job_id, _lease_caller)
    except Exception as exc:  # noqa: BLE001 - Beat's renew_all releases it later
        logger.warning("Could not leave the lease of reaped job %s: %s", job_id, exc)
    return run_id


def reap_generation_run(db: Session, job_id: str, status: str, reason: str) -> None:
    """Janitor hook: a reaped generation job (SIGKILLed worker; cooperative cancel had no one to
    see it) no longer leaves its run ``running`` for good."""
    recover_run(db, job_id, status, reason)


def reconcile_generation_runs(db: Session) -> list[str]:
    """Janitor reconciliation: a ``queued``/``running`` run whose every job is terminal gets the
    reap hook's transition (runs reaped before the hook existed, or whose hook failed)."""
    repaired: list[str] = []
    for _run_id, job in orphaned_runs(db):
        reason = f"Its job {job.id} ended {job.status} without updating the run" + (
            f": {job.error}" if job.error else "."
        )
        moved = recover_run(db, job.id, job.status, reason)
        if moved is not None:
            repaired.append(moved)
    return repaired


janitor.register_on_reap("generation_run", reap_generation_run)
janitor.register_reconciler("generation_runs", reconcile_generation_runs)


@worker_process_init.connect
def _install_generation(**_: Any) -> None:
    from ..services import labeling_ports
    from ..services.generation import install

    labeling_ports.install()
    install.install()
