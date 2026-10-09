"""Jobs: create, list, cancel, dismiss, and lease-aware dispatch (ADR-007, ADR-012; 5.7, 7.1).

Model-requirement queueing (R-03.64, X-08). A job may declare the miLLM model it needs. miLLM
serves one model at a time, so a job whose model differs from a model already in use stays
``queued`` with a reason naming the job and model it waits for. Jobs on the SAME model run
together under one lease (X-08: one lease per model; admission is miLLM's). A later job never
overtakes an earlier model-blocked one, so a busy model cannot starve another forever.

Dispatch is one sync implementation (``dispatch_queued``), run by the API after creating a job,
by every job as it finishes, and by Celery Beat. A PostgreSQL advisory lock serialises it, so two
dispatchers cannot both start conflicting jobs.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ..core.cancellation import plan_cancel
from ..core.clock import utc_now
from ..core.errors import ConflictError, NotFoundError
from ..core.ids import new_id
from ..core.job_kinds import get_job_kind
from ..models.job import TERMINAL_STATUSES, Job

logger = logging.getLogger(__name__)

#: Arbitrary constant key for pg_advisory_xact_lock around dispatch.
DISPATCH_LOCK_KEY = 0x6D69_4457  # "miDW"

#: Statuses that hold a model: the job is running, stopping, or already handed to a worker.
_HOLDING = ("running", "cancelling")


class _JobLike(Protocol):
    id: str
    required_model_id: str | None


@dataclass(frozen=True)
class DispatchDecision:
    dispatch: list[str]
    waiting: dict[str, str]  # job id -> reason


def plan_dispatch(active: Sequence[_JobLike], queued: Sequence[_JobLike]) -> DispatchDecision:
    """Which queued jobs may start now. Pure, so the rule is testable without a database.

    ``active`` are jobs holding a model (running, cancelling, or dispatched and not yet claimed);
    ``queued`` are undispatched jobs in creation order.
    """
    in_use: dict[str, str] = {}  # model -> holding job id
    for job in active:
        if job.required_model_id:
            in_use.setdefault(job.required_model_id, job.id)
    dispatch: list[str] = []
    waiting: dict[str, str] = {}
    blocked_earlier: str | None = None
    for job in queued:
        model = job.required_model_id
        if not model:
            dispatch.append(job.id)
            continue
        holder_model = next(iter(in_use), None)
        if blocked_earlier is not None:
            waiting[job.id] = (
                f"Waiting behind job {blocked_earlier}, which is queued for a different model."
            )
            continue
        if holder_model is None or model in in_use:
            dispatch.append(job.id)
            in_use.setdefault(model, job.id)
            continue
        holder = in_use[holder_model]
        waiting[job.id] = (
            f"Waiting: miLLM serves one model at a time, and job {holder} is using "
            f"{holder_model}. This job needs {model}."
        )
        blocked_earlier = job.id
    return DispatchDecision(dispatch, waiting)


def dispatch_queued(db: Session, *, send: Any = None) -> list[str]:
    """Start every queued job the model rule allows. Returns the ids sent to Celery."""
    from ..core.celery_app import celery_app

    sender = send or celery_app.send_task
    db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": DISPATCH_LOCK_KEY})
    active = list(
        db.execute(
            select(Job).where(
                (Job.status.in_(_HOLDING))
                | ((Job.status == "queued") & Job.celery_task_id.is_not(None))
            )
        ).scalars()
    )
    queued = list(
        db.execute(
            select(Job)
            .where(Job.status == "queued", Job.celery_task_id.is_(None))
            .order_by(Job.created_at, Job.id)
        ).scalars()
    )
    decision = plan_dispatch(active, queued)
    by_id = {job.id: job for job in queued}
    for job_id, reason in decision.waiting.items():
        by_id[job_id].queue_reason = reason
    to_send: list[tuple[Job, str]] = []
    for job_id in decision.dispatch:
        job = by_id[job_id]
        kind = get_job_kind(job.kind)
        if kind.task_name is None:
            job.queue_reason = f"No worker task is registered for {job.kind} jobs yet."
            continue
        job.celery_task_id = f"{job.id}-{uuid.uuid4().hex[:8]}"
        job.queue_reason = None
        to_send.append((job, kind.task_name))
    db.commit()  # record task ids before sending, so a fast worker finds them

    sent: list[str] = []
    for job, task_name in to_send:
        try:
            sender(task_name, args=[job.id], task_id=job.celery_task_id)
            sent.append(job.id)
        except Exception as exc:  # noqa: BLE001 - the broker being down must not lose the job
            logger.warning("Could not send job %s to Celery: %s", job.id, exc)
            db.execute(
                update(Job)
                .where(Job.id == job.id, Job.status == "queued")
                .values(celery_task_id=None, queue_reason="Waiting: the job broker is unreachable.")
            )
            db.commit()
    return sent


def claim_job(db: Session, job_id: str) -> Job | None:
    """Move a queued job to running, atomically. None when it was cancelled before starting
    (or another delivery of the same message already claimed it)."""
    now = utc_now()
    result = db.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == "queued")
        .values(status="running", started_at=now, heartbeat_at=now, queue_reason=None)
        .returning(Job.id)
    )
    claimed = result.scalar_one_or_none()
    db.commit()
    if claimed is None:
        return None
    return db.get(Job, job_id, populate_existing=True)


class JobService:
    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        kind: str,
        params: dict[str, Any],
        started_by: str,
        origin: str,
        required_model_id: str | None = None,
    ) -> Job:
        get_job_kind(kind)  # refuse an unregistered kind
        job = Job(
            id=new_id("job"),
            kind=kind,
            status="queued",
            progress=0.0,
            params=params,
            started_by=started_by,
            started_by_origin=origin,
            required_model_id=required_model_id,
        )
        db.add(job)
        await db.commit()
        return job

    @staticmethod
    async def get(db: AsyncSession, job_id: str) -> Job:
        job = await db.get(Job, job_id, populate_existing=True)
        if job is None:
            raise NotFoundError(f"No job {job_id}.", code="JOB_NOT_FOUND")
        return job

    @staticmethod
    async def list(
        db: AsyncSession, *, state: str | None = None, kind: str | None = None
    ) -> list[Job]:
        query = select(Job).order_by(Job.created_at.desc())
        if state == "active":
            query = query.where(Job.status.in_(("queued", "running", "cancelling")))
        elif state == "failed":
            query = query.where(Job.status == "failed", Job.dismissed_at.is_(None))
        elif state == "finished":
            query = query.where(Job.status.in_(tuple(TERMINAL_STATUSES)))
        if kind:
            query = query.where(Job.kind == kind)
        return list((await db.execute(query.limit(500))).scalars())

    @staticmethod
    async def cancel(db: AsyncSession, job_id: str, reason: str) -> tuple[Job, str]:
        job = (
            await db.execute(select(Job).where(Job.id == job_id).with_for_update())
        ).scalar_one_or_none()
        if job is None:
            raise NotFoundError(f"No job {job_id}.", code="JOB_NOT_FOUND")
        plan = plan_cancel(job.status)
        if not plan.allowed:
            raise ConflictError(plan.detail, code="JOB_TERMINAL", details={"status": job.status})
        now = utc_now()
        if plan.set_requested:
            job.cancel_requested_at = now
            job.message = reason
        if plan.new_status is not None:
            job.status = plan.new_status
            if plan.new_status in TERMINAL_STATUSES:
                job.completed_at = now
        task_id = job.celery_task_id
        await db.commit()
        if plan.new_status == "cancelled" and task_id:
            # Not the mechanism: only a message that has not started yet honours revoke. The row
            # is the channel. terminate= is deliberately absent (ADR-007).
            from ..core.celery_app import celery_app

            try:
                celery_app.control.revoke(task_id)
            except Exception:  # noqa: BLE001
                logger.warning("revoke() failed for %s; the row still says cancelled", task_id)
        return job, plan.detail

    @staticmethod
    async def dismiss(db: AsyncSession, job_id: str) -> Job:
        job = await JobService.get(db, job_id)
        if job.status != "failed":
            raise ConflictError(
                f"Only a failed job can be dismissed; this one is {job.status}.",
                code="JOB_NOT_FAILED",
                details={"status": job.status},
            )
        job.dismissed_at = utc_now()
        await db.commit()
        return job
