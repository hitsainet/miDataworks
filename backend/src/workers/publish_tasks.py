"""Feature 008's five Celery tasks on the ``publish`` queue (FR-008.54; FTID 008 section 7.3).

THE TOKEN. This module is the only caller of ``resolve_hf_token`` in feature 008 (FR-008.17;
``test_publish_wiring_ast.py``). The decrypted value lives in one local variable of
:func:`_hub_client`, is handed to ``HubClient`` (which keeps the ``HfApi`` object, not the string)
and is never put on a job row, a result, an emit payload, an approval or a log line.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from ..core.cancellation import CancelCheck, cooperative_cancel, record_progress
from ..core.celery_app import celery_app
from ..core.config import get_settings
from ..core.database import get_sync_db
from ..core.errors import AppError
from ..core.job_kinds import get_job_kind
from ..hub.hub_client import HubClient
from ..models.job import Job
from ..models.publish import (
    TERMINAL_PUBLISH_STATUSES,
    BuildStatus,
    CheckRunStatus,
    Publish,
    PublishBuild,
    PublishCheckRun,
    PublishStatus,
)
from ..services.job_service import claim_job, dispatch_queued
from ..services.publishing import build_service, publish_job
from . import janitor
from .emit import emit
from .secrets import resolve_hf_token

logger = logging.getLogger(__name__)

#: Seam for tests: how a HubClient is built from a token (the fake Hub replaces ``HfApi``).
HUB_API_FACTORY: Callable[..., Any] | None = None


def _hub_client(db: Session, job_id: str) -> HubClient | None:
    """The Hub client for this job, or None when no token is stored (C-2 then refuses)."""
    token = resolve_hf_token(db)
    if not token:
        return None
    settings = get_settings()
    kwargs: dict[str, Any] = {
        "retries": settings.publish_hub_retries,
        "backoff_s": settings.publish_hub_backoff_s,
        "on_wait": lambda message: record_progress(job_id, message=message, force=True),
    }
    if HUB_API_FACTORY is not None:
        kwargs["api_factory"] = HUB_API_FACTORY
    return HubClient(token, **kwargs)


def _next_jobs() -> None:
    try:
        with get_sync_db() as db:
            dispatch_queued(db)
    except Exception as exc:  # noqa: BLE001 - Beat dispatches again
        logger.warning("Could not dispatch the next queued job: %s", exc)


def _progress(job_id: str, kind: str) -> Callable[[str, float], None]:
    room = get_job_kind(kind).room(job_id)

    def report(phase: str, percent: float) -> None:
        record_progress(job_id, progress=percent, message=phase, force=True)
        emit(room, f"{kind}:progress", {"job_id": job_id, "phase": phase, "progress": percent})

    return report


def _finish(
    job_id: str, kind: str, status: str, result: dict[str, Any], error: str | None = None
) -> None:
    record_progress(job_id, status=status, progress=100.0, result=result, error=error)
    emit(get_job_kind(kind).room(job_id), f"{kind}:{status}", {"job_id": job_id, **result})


# --- publish_build ----------------------------------------------------------------------------


@cooperative_cancel
def run_build_job(job_id: str) -> dict[str, Any]:
    with get_sync_db() as db:
        job = claim_job(db, job_id)
        if job is None:
            return {"status": "skipped", "job_id": job_id}
        build_id = str(job.params["build_id"])
        checker = CancelCheck(job_id)

        def on_batch() -> None:
            checker.raise_if_cancelled("Cancelled while writing split files.")
            record_progress(job_id, message="Writing split files")

        try:
            build = build_service.run_build(db, build_id, on_batch=on_batch)
        except BaseException as exc:
            db.rollback()
            row = db.get(PublishBuild, build_id, populate_existing=True)
            if row is not None and row.status not in (BuildStatus.COMPLETED,):
                row.status = (
                    BuildStatus.CANCELLED if not isinstance(exc, Exception) else BuildStatus.FAILED
                )
                row.error = (
                    {"code": exc.code, "message": exc.message}
                    if isinstance(exc, AppError)
                    else {
                        "code": "build_failed",
                        "message": f"The build failed ({type(exc).__name__}).",
                    }
                )
                db.commit()
            if isinstance(exc, Exception):
                logger.exception("publish build %s failed", build_id)
                message = exc.message if isinstance(exc, AppError) else "The build failed."
                _finish(job_id, "publish_build", "failed", {"build_id": build_id}, error=message)
                return {"status": "failed", "build_id": build_id}
            raise
        result = {"build_id": build.id, "files": build.files}
    _finish(job_id, "publish_build", "completed", result)
    return {"status": "completed", **result}


@celery_app.task(name="midataworks.publish.build", acks_late=True)
def build(job_id: str) -> dict[str, Any]:
    try:
        return run_build_job(job_id)
    finally:
        _next_jobs()


# --- publish_check ----------------------------------------------------------------------------


@cooperative_cancel
def run_check_job(job_id: str) -> dict[str, Any]:
    with get_sync_db() as db:
        job = claim_job(db, job_id)
        if job is None:
            return {"status": "skipped", "job_id": job_id}
        run_id = str(job.params["check_run_id"])
        try:
            results = publish_job.run_check_run(db, run_id, _hub_client(db, job_id))
        except Exception as exc:
            db.rollback()
            logger.exception("check run %s failed", run_id)
            row = db.get(PublishCheckRun, run_id, populate_existing=True)
            if row is not None and row.completed_at is None:
                row.status = CheckRunStatus.FAILED
                db.commit()
            _finish(
                job_id,
                "publish_check",
                "failed",
                {"check_run_id": run_id},
                error=type(exc).__name__,
            )
            return {"status": "failed", "check_run_id": run_id}
    _finish(job_id, "publish_check", "completed", {"check_run_id": run_id, "results": results})
    return {"status": "completed", "check_run_id": run_id}


@celery_app.task(name="midataworks.publish.check", acks_late=True)
def check(job_id: str) -> dict[str, Any]:
    try:
        return run_check_job(job_id)
    finally:
        _next_jobs()


# --- publish ----------------------------------------------------------------------------------

_JOB_STATUS = {
    PublishStatus.PUBLISHED.value: "completed",
    PublishStatus.NO_CHANGE.value: "completed",
    PublishStatus.CANCELLED.value: "cancelled",
}


def run_publish_job(job_id: str) -> dict[str, Any]:
    with get_sync_db() as db:
        job = claim_job(db, job_id)
        if job is None:
            _settle_unstarted(db, job_id)
            return {"status": "skipped", "job_id": job_id}
        publish_id = str(job.params["publish_id"])
        checker = CancelCheck(job_id)
        result = publish_job.run_publish(
            db,
            publish_id,
            _hub_client(db, job_id),
            cancel_requested=checker.poll_now,
            progress=_progress(job_id, "publish"),
        )
    status = _JOB_STATUS.get(str(result["status"]), "failed")
    error = result.get("error") or None
    _finish(
        job_id,
        "publish",
        status,
        {"publish_id": publish_id, **result},
        error=(error or {}).get("message") if status == "failed" else None,
    )
    return {"publish_id": publish_id, **result}


def _settle_unstarted(db: Session, job_id: str) -> None:
    """A publish job cancelled before it started: its record must not hold the repository.

    ``claim_job`` returns None both for a job cancelled before it started AND for a second delivery
    of a job another worker (or 009's send, which runs the publish in process) already claimed. Only
    the first is settled: cancelling the record of a publish that is RUNNING would let a second
    publish to the same repository start beside it (found by 009, 2026-10-07).
    """
    from sqlalchemy import select

    job = db.get(Job, job_id, populate_existing=True)
    if job is None or job.status != "cancelled":
        return
    pub = db.execute(select(Publish).where(Publish.job_id == job_id)).scalar_one_or_none()
    if pub is not None and pub.status not in TERMINAL_PUBLISH_STATUSES:
        pub.status = PublishStatus.CANCELLED
        pub.error = {"code": "cancelled", "message": "Cancelled before it started."}
        db.commit()


def reap_publish(db: Session, job_id: str, status: str, reason: str) -> None:
    """Janitor hook: a reaped publish job fails (or is cancelled) its publish record too."""
    from sqlalchemy import select

    pub = db.execute(select(Publish).where(Publish.job_id == job_id)).scalar_one_or_none()
    if pub is None or pub.status in TERMINAL_PUBLISH_STATUSES:
        return
    pub.status = PublishStatus.CANCELLED if status == "cancelled" else PublishStatus.FAILED
    pub.error = {"code": "worker_lost", "message": reason}
    db.commit()


janitor.register_on_reap("publish", reap_publish)


@celery_app.task(name="midataworks.publish.publish", acks_late=True)
def publish(job_id: str) -> dict[str, Any]:
    try:
        return run_publish_job(job_id)
    finally:
        _next_jobs()


# --- publish_reverify -------------------------------------------------------------------------


def run_reverify_job(job_id: str) -> dict[str, Any]:
    with get_sync_db() as db:
        job = claim_job(db, job_id)
        if job is None:
            return {"status": "skipped", "job_id": job_id}
        publish_id = str(job.params["publish_id"])
        hub = _hub_client(db, job_id)
        if hub is None:
            _finish(
                job_id,
                "publish_reverify",
                "failed",
                {"publish_id": publish_id},
                error="No Hugging Face token is stored; add one in Settings to re-verify.",
            )
            return {"status": "failed"}
        try:
            result = publish_job.run_reverify(db, publish_id, hub)
        except Exception as exc:  # noqa: BLE001 - reported on the job without the Hub's text
            logger.exception("re-verify of %s failed", publish_id)
            _finish(
                job_id,
                "publish_reverify",
                "failed",
                {"publish_id": publish_id},
                error=f"Re-verify failed ({type(exc).__name__}).",
            )
            return {"status": "failed"}
    _finish(job_id, "publish_reverify", "completed", {"publish_id": publish_id, **result})
    return result


@celery_app.task(name="midataworks.publish.reverify", acks_late=True)
def reverify(job_id: str) -> dict[str, Any]:
    try:
        return run_reverify_job(job_id)
    finally:
        _next_jobs()


# --- export -----------------------------------------------------------------------------------


def run_export_job(job_id: str) -> dict[str, Any]:
    from ..services.exports import export_service

    with get_sync_db() as db:
        job = claim_job(db, job_id)
        if job is None:
            return {"status": "skipped", "job_id": job_id}
        export_id = str(job.params["export_id"])
        result = export_service.run_export(db, export_id)
    status = "completed" if result["status"] == "completed" else "failed"
    _finish(
        job_id,
        "export",
        status,
        {"export_id": export_id, **result},
        error=(result.get("error") or {}).get("message") if status == "failed" else None,
    )
    return {"export_id": export_id, **result}


@celery_app.task(name="midataworks.publish.export", acks_late=True)
def export(job_id: str) -> dict[str, Any]:
    try:
        return run_export_job(job_id)
    finally:
        _next_jobs()
