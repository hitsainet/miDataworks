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

from ..core.cancellation import CancelCheck, cooperative_cancel, record_progress
from ..core.celery_app import celery_app
from ..core.config import get_settings
from ..core.database import get_sync_db
from ..models.label_run import LabelRun, LabelRunJob
from ..services import label_inputs
from ..services.job_service import claim_job, dispatch_queued
from ..services.label_run_engine import LabelRunEngine, RunStop, SampleSpec, keep_share
from ..services.label_run_service import set_state

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


@worker_process_init.connect
def _install_labeling(**_: Any) -> None:
    from ..services import labeling_ports

    labeling_ports.install()
