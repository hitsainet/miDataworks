"""Jobs REST (ADR-007; Foundation task 5.7) and the self-test job (task 5.8)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.agent_origin import Actor, get_actor, resolve_who
from ....core.database import get_db, get_sync_db
from ....core.job_kinds import get_job_kind
from ....models.job import Job
from ....schemas.jobs import CancelOut, CancelRequest, JobList, JobOut, SelftestRequest
from ....services.job_service import JobService, dispatch_queued

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


def to_out(job: Job) -> JobOut:
    data = {column: getattr(job, column) for column in JobOut.model_fields if column != "room"}
    return JobOut(**data, room=get_job_kind(job.kind).room(job.id))


def _dispatch() -> list[str]:
    with get_sync_db() as session:
        return dispatch_queued(session)


@router.get("", response_model=JobList)
async def list_jobs(
    state: Literal["active", "failed", "finished"] | None = Query(None),
    kind: str | None = Query(None, max_length=64),
    db: AsyncSession = Depends(get_db),
) -> JobList:
    return JobList(jobs=[to_out(j) for j in await JobService.list(db, state=state, kind=kind)])


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: str, db: AsyncSession = Depends(get_db)) -> JobOut:
    return to_out(await JobService.get(db, job_id))


@router.post("/{job_id}/cancel", response_model=CancelOut, status_code=202)
async def cancel_job(
    job_id: str,
    body: CancelRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> CancelOut:
    """Ask a job to stop. 409 when it is already finished."""
    reason = body.reason if body else "Cancelled by the operator."
    job, detail = await JobService.cancel(db, job_id, reason)
    return CancelOut(job=to_out(job), detail=detail)


@router.post("/{job_id}/dismiss", response_model=JobOut)
async def dismiss_job(job_id: str, db: AsyncSession = Depends(get_db)) -> JobOut:
    """Hide a failed job from the failed-operations list. The record is kept."""
    return to_out(await JobService.dismiss(db, job_id))


@router.post("/selftest", response_model=JobOut, status_code=201)
async def start_selftest(
    body: SelftestRequest,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> JobOut:
    """Start the walking-skeleton job: Parquet through the writer, DuckDB read-back, progress."""
    who = await resolve_who(actor, db)
    job = await JobService.create(
        db,
        kind="selftest",
        params=body.model_dump(exclude={"required_model_id"}),
        started_by=who.who,
        origin=who.origin,
        required_model_id=body.required_model_id,
    )
    await run_in_threadpool(_dispatch)
    return to_out(await JobService.get(db, job.id))
