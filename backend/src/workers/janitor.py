# Origin: miStudio (Onegaishimas/miStudio) backend/src/workers/cleanup_orphaned_tasks.py @ c829a2cc
# Mode: adapt (docs/REUSE.md). Kept: deliberately not a retry; a reason the operator can act on.
# Changed: one sweep over dw_jobs with a per-kind limit from the job-kind registry, and the
# "Celery does not report it active" condition is required as well as a stale heartbeat.
"""The job janitor (ADR-007; Foundation task 5.5).

A job is reaped only when BOTH hold:
1. its last sign of life (``heartbeat_at``, else ``started_at``, else ``created_at``) is older
   than its kind's janitor limit; and
2. Celery does not report its task as active.

A running job becomes ``failed`` with a reason; a ``cancelling`` job whose worker died becomes
``cancelled`` (the operator asked for that). Queued jobs are never judged on age: work waiting
behind a long job is legitimately old. Nothing is retried automatically.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..core.celery_app import celery_app
from ..core.clock import utc_now
from ..core.job_kinds import JOB_KINDS
from ..models.job import Job
from .heartbeat import active_task_ids, is_stale, last_sign_of_life

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Reaped:
    job_id: str
    status: str
    reason: str


def sweep(
    db: Session,
    *,
    now: datetime | None = None,
    active_ids: set[str] | None = None,
    limits: dict[str, timedelta] | None = None,
    ask_celery: bool = True,
) -> list[Reaped]:
    """Reap stale jobs. ``active_ids`` and ``limits`` are overridable for tests."""
    now = now or utc_now()
    if active_ids is None and ask_celery:
        active_ids = active_task_ids()
        if active_ids is None:
            logger.warning("Janitor skipped: cannot tell which tasks Celery is running")
            return []
    active = active_ids or set()
    reaped: list[Reaped] = []
    candidates = db.execute(select(Job).where(Job.status.in_(("running", "cancelling")))).scalars()
    for job in list(candidates):
        kind = JOB_KINDS.get(job.kind)
        if kind is None:
            logger.warning("Job %s has unregistered kind %s; not judged", job.id, job.kind)
            continue
        limit = (limits or {}).get(job.kind, kind.janitor_heartbeat_limit)
        last = last_sign_of_life(job.heartbeat_at, job.started_at, job.created_at)
        if not is_stale(last, limit, now):
            continue
        if job.celery_task_id and job.celery_task_id in active:
            continue
        new_status = "cancelled" if job.status == "cancelling" else "failed"
        reason = (
            f"The worker stopped reporting: no heartbeat since {last.isoformat()} (limit "
            f"{int(limit.total_seconds())} s) and Celery does not report the task running. "
            "It was not retried; start it again if you still need it."
        )
        result = db.execute(
            update(Job)
            .where(Job.id == job.id, Job.status == job.status)
            .values(status=new_status, error=reason, completed_at=now)
        )
        if result.rowcount:  # type: ignore[attr-defined]
            reaped.append(Reaped(job.id, new_status, reason))
    db.commit()
    for item in reaped:
        logger.warning("Janitor marked job %s %s", item.job_id, item.status)
    return reaped


@celery_app.task(name="midataworks.system.janitor")
def janitor_task() -> dict[str, object]:
    from ..core.database import get_sync_db

    with get_sync_db() as db:
        reaped = sweep(db)
    return {"reaped": [r.job_id for r in reaped]}
