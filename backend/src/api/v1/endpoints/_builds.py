"""The shared body of ``POST /versions`` and ``POST /recipes/{id}/build`` (FR-002.5, FR-002.16)."""

from __future__ import annotations

from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.agent_origin import Actor, resolve_who
from ....core.database import get_sync_db
from ....core.errors import AppError
from ....schemas.versions import BuildAccepted, VersionBuildRequest
from ....services import version_build_service
from ....services.job_service import dispatch_queued
from ....services.operator_port import OperatorRefusal
from ....services.version_read_service import version_out


def _dispatch() -> list[str]:
    with get_sync_db() as session:
        return dispatch_queued(session)


async def respond(db: AsyncSession, actor: Actor, req: VersionBuildRequest) -> JSONResponse:
    """``200`` with the existing version, or ``202`` with the job (new or already running)."""
    who = await resolve_who(actor, db)
    try:
        outcome = await version_build_service.request_build(db, req, who, actor)
    except OperatorRefusal as refusal:  # a registry refusal that escaped validation
        raise AppError(refusal.message, code=refusal.code, status_code=422) from None
    if outcome.kind == "existing":
        assert outcome.version_id is not None
        out = await version_out(db, outcome.version_id)
        return JSONResponse(status_code=200, content=out.model_dump(mode="json"))
    if outcome.kind == "started":
        await run_in_threadpool(_dispatch)
    assert outcome.job_id is not None
    accepted = BuildAccepted(
        job_id=outcome.job_id,
        existing_job=outcome.kind == "running",
        seed=outcome.seed,
        request_digest=outcome.request_digest,
    )
    return JSONResponse(status_code=202, content=accepted.model_dump(mode="json"))
