"""Celery tasks for feature 002 (FR-002.6, 002.35, 002.36; FTID 002 section 3.5).

- ``midataworks.versions.advance_build`` — one idempotent pass of the orchestrator; also the link
  callback feature 003's executor sends when a step ends. Queue ``curation``.
- ``midataworks.versions.verify_rebuild`` — the same pass for a verify job. Queue ``curation``.
- ``midataworks.versions.version_orphan_sweeper`` — Beat, hourly. Queue ``default``.

Importing this module also registers the ``version_build`` liveness probe with the janitor: a build
waiting on a step that feature 003 is computing is alive while that step's heartbeat is recent,
even though the build's own task has returned (FTASKS 6.8).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.cancellation import cooperative_cancel, record_progress
from ..core.celery_app import celery_app
from ..core.database import get_sync_db
from ..models.job import Job
from ..models.step_execution import StepExecution
from ..models.version import VersionBuild
from ..services import build_orchestrator
from ..services.job_service import dispatch_queued
from ..services.version_sweeper import sweep_orphans
from . import janitor
from .emit import emit

logger = logging.getLogger(__name__)


def _send_task(name: str, **kwargs: Any) -> Any:
    return celery_app.send_task(name, **kwargs)


def _next_jobs() -> None:
    try:
        with get_sync_db() as db:
            dispatch_queued(db)
    except Exception as exc:  # noqa: BLE001 - Beat dispatches again
        logger.warning("Could not dispatch the next queued job: %s", exc)


@cooperative_cancel
def run_pass(job_id: str) -> dict[str, Any]:
    with get_sync_db() as session:
        try:
            outcome = build_orchestrator.advance(session, job_id, emit=emit, send_task=_send_task)
        except Exception as exc:
            logger.exception("build %s failed unexpectedly", job_id)
            session.rollback()
            record_progress(
                job_id,
                status="failed",
                error="The build failed unexpectedly; the log carries the details.",
                result={"error": {"code": "INTERNAL_ERROR", "message": type(exc).__name__}},
            )
            outcome = "failed"
    return {"job_id": job_id, "outcome": outcome}


def _run(job_id: str) -> dict[str, Any]:
    result = run_pass(job_id)
    if (
        result.get("outcome") in {"completed", "failed", "skipped"}
        or result.get("status") == "cancelled"
    ):
        _next_jobs()
    return result


@celery_app.task(name="midataworks.versions.advance_build", acks_late=True)
def advance_build(job_id: str, *_link_args: Any) -> dict[str, Any]:
    return _run(job_id)


@celery_app.task(name="midataworks.versions.verify_rebuild", acks_late=True)
def verify_rebuild(job_id: str, *_link_args: Any) -> dict[str, Any]:
    return _run(job_id)


@celery_app.task(name="midataworks.versions.version_orphan_sweeper")
def version_orphan_sweeper() -> dict[str, Any]:
    with get_sync_db() as session:
        removed = sweep_orphans(session)
    return {"removed": removed}


def build_liveness(session: Session, job: Job) -> datetime | None:
    """The newest heartbeat of a step this build is computing or waiting on (FTASKS 6.8)."""
    build = session.get(VersionBuild, job.id)
    if build is None:
        return None
    ids = [build.waiting_execution_id] if build.waiting_execution_id else []
    ids.extend(
        session.execute(
            select(StepExecution.id).where(
                StepExecution.job_id == job.id, StepExecution.state == "running"
            )
        ).scalars()
    )
    if not ids:
        return None
    beats = session.execute(
        select(StepExecution.heartbeat_at).where(StepExecution.id.in_(ids))
    ).scalars()
    known = [b for b in beats if b is not None]
    return max(known) if known else None


janitor.register_liveness("version_build", build_liveness)
janitor.register_liveness("version_verify", build_liveness)
