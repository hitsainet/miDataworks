"""Celery tasks for feature 005 (FR-005.19, FR-005.21, FR-005.30, FR-005.45; FTID 005 section 9).

- ``midataworks.labeling.run_label_run`` — a classifier or judge run (job kind ``label_run``);
- ``midataworks.labeling.run_label_preview`` — the keep-share estimate (``label_preview``);
- ``midataworks.labeling.run_rederive`` — new thresholds, no endpoint call (``label_rederive``);
- ``midataworks.labeling.run_aggregate`` — several judges (``label_aggregate``).

All four run on the ``labeling`` queue. Importing this module in a worker also installs feature
005 into 003's endpoint port (``labeling_ports.install``) at worker start.
"""

from __future__ import annotations

import logging
from typing import Any

from celery.signals import worker_process_init
from sqlalchemy.orm import Session

from ..core.cancellation import CancelCheck, cooperative_cancel, record_progress
from ..core.celery_app import celery_app
from ..core.config import get_settings
from ..core.database import get_sync_db
from ..models.label_run import LabelRun, LabelRunJob
from ..services import label_inputs
from ..services.job_service import claim_job, dispatch_queued
from ..services.label_run_engine import LabelRunEngine, RunStop, SampleSpec, keep_share
from ..services.label_run_service import end_after_worker_lost, orphaned_runs, set_state
from ..services.model_lease_holder import HOLDER
from . import janitor

logger = logging.getLogger(__name__)


def _engine() -> LabelRunEngine:
    """The engine a task uses (tests replace this to inject fakes)."""
    return LabelRunEngine()


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
            logger.exception("label job %s failed unexpectedly", job_id)
            session.rollback()
            link = session.query(LabelRunJob).filter(LabelRunJob.job_id == job_id).one_or_none()
            if link is not None:
                run = session.get(LabelRun, link.label_run_id, populate_existing=True)
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
                error="The label job failed unexpectedly; the log carries the details.",
                result={"error": {"code": "INTERNAL_ERROR"}},
            )
            return {"job_id": job_id, "outcome": "failed"}


def _run(job_id: str) -> dict[str, Any]:
    try:
        return run_job(job_id)
    finally:
        _next_jobs()


@celery_app.task(name="midataworks.labeling.run_label_run", acks_late=True)
def run_label_run(job_id: str) -> dict[str, Any]:
    return _run(job_id)


@celery_app.task(name="midataworks.labeling.run_rederive", acks_late=True)
def run_rederive(job_id: str) -> dict[str, Any]:
    return _run(job_id)


@celery_app.task(name="midataworks.labeling.run_aggregate", acks_late=True)
def run_aggregate(job_id: str) -> dict[str, Any]:
    return _run(job_id)


@cooperative_cancel
def preview_job(job_id: str) -> dict[str, Any]:
    """Score a reservoir sample and record share, interval and sample size in the job's result.
    Writes no labels and takes no lease (FR-005.19, FR-005.20)."""
    from ..models.decision_template import DecisionTemplate
    from ..models.version import Version
    from ..services.endpoint_resolver import resolve

    with get_sync_db() as session:
        job = claim_job(session, job_id)
        if job is None:
            return {"job_id": job_id, "outcome": "skipped"}
        params = dict(job.params)
        try:
            resolved = resolve("classifier", session)
            template = session.get(DecisionTemplate, params["template_id"])
            version = session.get(Version, params["input_version_id"])
            if template is None or version is None:
                raise RunStop("NOT_FOUND", "The template or the version no longer exists.")
            files = label_inputs.version_files(version.splits)
            settings = get_settings()
            n = min(
                int(params.get("sample_rows") or settings.keep_share_sample_rows),
                settings.keep_share_max_rows,
            )
            rows = label_inputs.sample_rows(
                files, params["field_map"], params.get("row_filter"), n, int(params["seed"])
            )
            spec = SampleSpec(
                role="classifier",
                protocol=resolved.protocol,
                base_url=resolved.base_url,
                model_id=resolved.model_id,
                api_key=resolved.api_key,
                key_origin=resolved.key_origin,
                template_body=template.body,
                rubric_body=None,
                question=params.get("question"),
                threshold_positive=params.get("threshold_positive"),
                threshold_negative=params.get("threshold_negative"),
                min_top_probability=params.get("min_top_probability"),
                label_set=list(template.body.get("label_set") or []),
                server_kind=str(params.get("server_kind") or "openai_compatible"),
            )
            cancel = CancelCheck(job_id)
            result = keep_share(
                spec,
                rows,
                lambda: cancel.raise_if_cancelled("Stopped estimating the keep share."),
            )
        except RunStop as stop:
            record_progress(
                job_id,
                status="failed",
                error=stop.message,
                result={"error": {"code": stop.code, "message": stop.message}},
            )
            return {"job_id": job_id, "outcome": "failed"}
        except Exception as exc:  # noqa: BLE001 - a preview failure is reported, never raised
            logger.warning("keep-share preview %s failed: %s", job_id, type(exc).__name__)
            record_progress(
                job_id,
                status="failed",
                error=str(getattr(exc, "message", "")) or type(exc).__name__,
                result={"error": {"code": getattr(exc, "code", "INTERNAL_ERROR")}},
            )
            return {"job_id": job_id, "outcome": "failed"}
        result["seed"] = int(params["seed"])
        record_progress(job_id, status="completed", progress=100.0, result=result)
        return {"job_id": job_id, "outcome": "completed"}


@celery_app.task(name="midataworks.labeling.run_label_preview", acks_late=True)
def run_label_preview(job_id: str) -> dict[str, Any]:
    try:
        return preview_job(job_id)
    finally:
        _next_jobs()


# --- the janitor (005 FTASKS 9.5; live defect 2026-10-18) ---------------------------------------


def _lease_caller(base_url: str) -> Any:
    from ..services.label_run_service import caller_for

    return caller_for(base_url, None)


def recover_run(db: Session, job_id: str, status: str, reason: str) -> str | None:
    """Move the run of a job that ended without its worker to a resumable state, and leave the
    miLLM lease the job still holds (the last member releases it at once, rather than at Beat's
    next cleanup or the lease's TTL)."""
    run_id = end_after_worker_lost(db, job_id, status, reason)
    try:
        HOLDER.leave_for(job_id, _lease_caller)
    except Exception as exc:  # noqa: BLE001 - Beat's renew_all releases it later
        logger.warning("Could not leave the lease of reaped job %s: %s", job_id, exc)
    return run_id


def reap_label_run(db: Session, job_id: str, status: str, reason: str) -> None:
    """Janitor hook: a reaped label job (SIGKILLed worker; cooperative cancel had no one to see
    it) no longer leaves its run ``running`` for good."""
    recover_run(db, job_id, status, reason)


def reconcile_label_runs(db: Session) -> list[str]:
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


for _kind in ("label_run", "label_rederive", "label_aggregate"):
    janitor.register_on_reap(_kind, reap_label_run)
janitor.register_reconciler("label_runs", reconcile_label_runs)


@worker_process_init.connect
def _install_labeling(**_: Any) -> None:
    from ..services import labeling_ports

    labeling_ports.install()
