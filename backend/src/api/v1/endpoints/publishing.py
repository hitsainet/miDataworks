"""Publishing REST (FR-008.1, 008.2, 008.15, 008.37, 008.50–008.53, 008.56, 008.57; FTDD 008
section 5.2).

Two routes write to the Hub and carry ``@requires_approval_when_agent("hub_push")``:
``POST /publishes`` and ``POST /publishes/{id}/card``. An agent call is stored and answered ``202``;
nothing reaches the Hub until the operator approves, and then the approved request is replayed
once. The approval digest (``publish_digest``) is computed by ``enrich`` BEFORE the payload is
digested, and the service re-checks it (``publish_service.check_authorization``), so the REST gate
and the service gate both have to agree.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.agent_origin import Actor, get_actor, requires_approval_when_agent, resolve_who
from ....core.database import get_db, get_sync_db
from ....core.errors import ConflictError, NotFoundError
from ....core.ids import new_id
from ....models.job import Job
from ....models.publish import (
    CheckRunStatus,
    Publish,
    PublishBuild,
    PublishCheckRun,
    PublishFile,
)
from ....schemas.publishing import (
    BuildAccepted,
    BuildIn,
    BuildOut,
    CardDraftOut,
    CardIn,
    CheckRunAccepted,
    CheckRunOut,
    ChecksIn,
    JobAccepted,
    LicenceTableOut,
    PublishAccepted,
    PublishIn,
    PublishList,
    PublishOut,
)
from ....services.job_service import dispatch_queued
from ....services.publishing import build_service, publish_service
from ....services.publishing.licence_table import TABLE, LicenceClass
from ._paging import Page, paging

router = APIRouter(prefix="/api/v1", tags=["publishing"])


def _dispatch() -> list[str]:
    with get_sync_db() as session:
        return dispatch_queued(session)


# --- builds -----------------------------------------------------------------------------------


def _build_out(b: PublishBuild) -> BuildOut:
    return BuildOut(
        id=b.id,
        version_id=b.version_id,
        status=b.status,
        projection=b.projection,
        files=b.files,
        columns=b.columns,
        omitted=b.omitted,
        error=b.error,
        job_id=b.job_id,
        created_at=b.created_at,
        completed_at=b.completed_at,
    )


@router.post(
    "/versions/{version_id}/publish-builds",
    response_model=BuildAccepted,
    status_code=202,
    responses={200: {"model": BuildAccepted}},
)
async def start_build(
    version_id: str,
    body: BuildIn,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> JSONResponse:
    """Preview files: build one Parquet file per split (``200`` when a matching build exists)."""
    who = await resolve_who(actor, db)

    def run() -> tuple[str, str | None, bool, str]:
        with get_sync_db() as session:
            out = build_service.request_build(
                session, version_id, body.label_column, started_by=who.who, origin=who.origin
            )
            return out.build.id, out.job.id if out.job else None, out.reused, out.build.status

    build_id, job_id, reused, status = await run_in_threadpool(run)
    if job_id is not None:
        await run_in_threadpool(_dispatch)
    accepted = BuildAccepted(build_id=build_id, job_id=job_id, reused=reused, status=status)
    return JSONResponse(status_code=200 if reused else 202, content=accepted.model_dump())


@router.get("/publish-builds/{build_id}", response_model=BuildOut)
async def get_build(build_id: str, db: AsyncSession = Depends(get_db)) -> BuildOut:
    build = await db.get(PublishBuild, build_id, populate_existing=True)
    if build is None:
        raise NotFoundError(f"No publish build {build_id}.", code="build_not_found")
    return _build_out(build)


# --- checks -----------------------------------------------------------------------------------


@router.post(
    "/versions/{version_id}/publish-checks", response_model=CheckRunAccepted, status_code=202
)
async def start_checks(
    version_id: str,
    body: ChecksIn,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> CheckRunAccepted:
    """Run C-1..C-7 as a preview. A publish never trusts this; it re-runs them itself."""
    who = await resolve_who(actor, db)

    def run() -> CheckRunAccepted:
        with get_sync_db() as session:
            version = build_service.version_or_refuse(session, version_id)
            build = build_service.completed_build(session, body.build_id, version.id)
            run_id = new_id("pchk")
            job = Job(
                id=new_id("job"),
                kind="publish_check",
                status="queued",
                progress=0.0,
                params={"check_run_id": run_id},
                started_by=who.who,
                started_by_origin=who.origin,
            )
            session.add(job)
            session.flush()
            session.add(
                PublishCheckRun(
                    id=run_id,
                    version_id=version.id,
                    build_id=build.id,
                    repo_id=body.repo_id,
                    requested_visibility=body.visibility,
                    job_id=job.id,
                    status=CheckRunStatus.QUEUED,
                )
            )
            session.commit()
            return CheckRunAccepted(check_run_id=run_id, job_id=job.id)

    accepted = await run_in_threadpool(run)
    await run_in_threadpool(_dispatch)
    return accepted


@router.get("/publish-check-runs/{check_run_id}", response_model=CheckRunOut)
async def get_check_run(check_run_id: str, db: AsyncSession = Depends(get_db)) -> CheckRunOut:
    run = await db.get(PublishCheckRun, check_run_id, populate_existing=True)
    if run is None:
        raise NotFoundError(f"No check run {check_run_id}.", code="check_run_not_found")
    return CheckRunOut(
        id=run.id,
        version_id=run.version_id,
        build_id=run.build_id,
        repo_id=run.repo_id,
        requested_visibility=run.requested_visibility,
        status=run.status,
        results=run.results,
        licence_table_version=run.licence_table_version,
        job_id=run.job_id,
        created_at=run.created_at,
        completed_at=run.completed_at,
    )


# --- card draft and the handoff manifest ------------------------------------------------------


@router.get("/versions/{version_id}/card-draft", response_model=CardDraftOut)
async def card_draft(
    version_id: str,
    repo_id: str | None = Query(None, max_length=200),
    build_id: str | None = Query(None, max_length=40),
) -> CardDraftOut:
    """The generated card: locked front matter and record, default prose (FR-008.8)."""
    from ....services.publishing import drafts

    def run() -> CardDraftOut:
        with get_sync_db() as session:
            return CardDraftOut(**drafts.card_draft(session, version_id, repo_id, build_id))

    return await run_in_threadpool(run)


@router.get(
    "/versions/{version_id}/handoff-manifest",
    response_class=Response,
    responses={200: {"content": {"application/json": {}}}},
)
async def handoff_manifest(version_id: str) -> Response:
    """``midataworks.dataset-version/v1``: the published manifest when one exists, else the
    built one (FR-008.37). 002 owns ``/versions/{id}/manifest`` (Stage 3)."""
    from ....services.publishing import drafts

    def run() -> bytes:
        with get_sync_db() as session:
            return drafts.handoff_manifest_bytes(session, version_id)

    content = await run_in_threadpool(run)
    from ....services.identity import bytes_sha256

    return Response(
        content=content, media_type="application/json", headers={"ETag": bytes_sha256(content)}
    )


# --- publishes --------------------------------------------------------------------------------


async def _publish_facts(values: dict[str, Any], db: AsyncSession) -> dict[str, Any]:
    """What the approval card shows, and the digest the approval binds to (FR-008.51)."""

    def run() -> dict[str, Any]:
        with get_sync_db() as session:
            if "publish_id" in values:
                req = publish_service.card_republish_request(
                    session, values["publish_id"], values["body"].card_prose
                )
            else:
                body: PublishIn = values["body"]
                req = publish_service.PublishRequest(
                    version_id=body.version_id,
                    build_id=body.build_id,
                    repo_id=body.repo_id,
                    visibility=body.visibility,
                    card_prose=body.card_prose,
                )
            digest = publish_service.request_digest(session, req)
            return {
                "publish_digest": digest,
                "facts": {
                    "kind": req.kind,
                    "version_id": req.version_id,
                    "build_id": req.build_id,
                    "repo_id": req.repo_id,
                    "visibility": req.visibility,
                },
            }

    return await run_in_threadpool(run)


def _summary(payload: dict[str, Any]) -> str:
    facts = payload.get("facts") or {}
    return (
        f"Push version {facts.get('version_id')} to {facts.get('repo_id')} "
        f"({facts.get('visibility')}, {facts.get('kind')})"
    )


def _authorization(actor: Actor) -> publish_service.Authorization:
    if actor.origin == "operator":
        return publish_service.OperatorOrigin()
    assert actor.approval_id is not None  # the gate let only an approved agent call through
    return publish_service.PublishApproval(actor.approval_id)


async def _create(db: AsyncSession, actor: Actor, make: Any) -> JSONResponse:
    who = await resolve_who(actor, db)

    def run() -> PublishAccepted:
        with get_sync_db() as session:
            req = make(session)
            created = publish_service.request_publish(
                session,
                req,
                _authorization(actor),
                publish_service.Who(who.who, who.origin),
            )
            return PublishAccepted(
                publish_id=created.publish.id,
                job_id=created.job.id,
                request_digest=created.publish.request_digest,
            )

    accepted = await run_in_threadpool(run)
    await run_in_threadpool(_dispatch)
    return JSONResponse(status_code=201, content=accepted.model_dump())


@router.post(
    "/publishes",
    response_model=PublishAccepted,
    status_code=201,
    responses={202: {"description": "Agent request waiting for the operator's approval"}},
)
@requires_approval_when_agent("hub_push", enrich=_publish_facts, summary=_summary)
async def create_publish(
    body: PublishIn,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> JSONResponse:
    """Publish a built version to the Hub. Omitted visibility is private (FR-008.2)."""

    def make(_: Any) -> publish_service.PublishRequest:
        return publish_service.PublishRequest(
            version_id=body.version_id,
            build_id=body.build_id,
            repo_id=body.repo_id,
            visibility=body.visibility,
            card_prose=body.card_prose,
        )

    return await _create(db, actor, make)


@router.post(
    "/publishes/{publish_id}/card",
    response_model=PublishAccepted,
    status_code=201,
    responses={202: {"description": "Agent request waiting for the operator's approval"}},
)
@requires_approval_when_agent("hub_push", enrich=_publish_facts, summary=_summary)
async def republish_card(
    publish_id: str,
    body: CardIn,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> JSONResponse:
    """A card-only republish of a published version (FR-008.56)."""

    def make(session: Any) -> publish_service.PublishRequest:
        return publish_service.card_republish_request(session, publish_id, body.card_prose)

    return await _create(db, actor, make)


@router.post("/publishes/{publish_id}/reverify", response_model=JobAccepted, status_code=202)
async def reverify(
    publish_id: str, db: AsyncSession = Depends(get_db), actor: Actor = Depends(get_actor)
) -> JobAccepted:
    """Compare the recorded files with the Hub at the recorded commit (FR-008.57). No Hub write."""
    who = await resolve_who(actor, db)

    def run() -> JobAccepted:
        with get_sync_db() as session:
            pub = publish_service.get_publish(session, publish_id)
            if pub.commit is None:
                raise ConflictError(
                    f"Publish {publish_id} recorded no commit; there is nothing to re-verify.",
                    code="publish_has_no_commit",
                )
            job = Job(
                id=new_id("job"),
                kind="publish_reverify",
                status="queued",
                progress=0.0,
                params={"publish_id": publish_id},
                started_by=who.who,
                started_by_origin=who.origin,
            )
            session.add(job)
            session.commit()
            return JobAccepted(job_id=job.id)

    accepted = await run_in_threadpool(run)
    await run_in_threadpool(_dispatch)
    return accepted


async def _publish_out(db: AsyncSession, p: Publish, with_files: bool) -> PublishOut:
    files: list[dict[str, Any]] = []
    if with_files:
        rows = (
            await db.execute(select(PublishFile).where(PublishFile.publish_id == p.id))
        ).scalars()
        files = [
            {
                "path": f.path_in_repo,
                "role": f.role,
                "split": f.split,
                "bytes": f.bytes,
                "sha256": f.sha256,
                "git_blob_sha1": f.git_blob_sha1,
                "remote_lfs_sha256": f.remote_lfs_sha256,
                "remote_blob_id": f.remote_blob_id,
                "match": f.match,
            }
            for f in rows
        ]
    return PublishOut(
        id=p.id,
        job_id=p.job_id,
        version_id=p.version_id,
        build_id=p.build_id,
        kind=p.kind,
        parent_publish_id=p.parent_publish_id,
        repo_id=p.repo_id,
        requested_visibility=p.requested_visibility,
        visibility_after=p.visibility_after,
        repo_existed=p.repo_existed,
        repo_was_private=p.repo_was_private,
        head_before=p.head_before,
        commit=p.commit,
        status=p.status,
        card_sha256=p.card_sha256,
        manifest_sha256=p.manifest_sha256,
        check_snapshot=p.check_snapshot,
        licence_table_version=p.licence_table_version,
        request_digest=p.request_digest,
        approval_id=p.approval_id,
        send_id=p.send_id,
        approved_by=p.approved_by,
        started_by=p.started_by,
        started_by_origin=p.started_by_origin,
        cancel_too_late=p.cancel_too_late,
        timings=p.timings,
        error=p.error,
        created_at=p.created_at,
        completed_at=p.completed_at,
        files=files,
    )


@router.get("/publishes", response_model=PublishList)
async def list_publishes(
    version_id: str | None = Query(None, max_length=64),
    repo_id: str | None = Query(None, max_length=200),
    page: Page = Depends(paging),
    db: AsyncSession = Depends(get_db),
) -> PublishList:
    query = select(Publish)
    count = select(func.count()).select_from(Publish)
    if version_id:
        query = query.where(Publish.version_id == version_id)
        count = count.where(Publish.version_id == version_id)
    if repo_id:
        query = query.where(Publish.repo_id == repo_id)
        count = count.where(Publish.repo_id == repo_id)
    total = int((await db.execute(count)).scalar_one())
    rows = (
        await db.execute(
            query.order_by(Publish.created_at.desc(), Publish.id)
            .offset((page.page - 1) * page.limit)
            .limit(page.limit)
        )
    ).scalars()
    items = [await _publish_out(db, p, False) for p in rows]
    return PublishList(items=items, total=total, page=page.page, limit=page.limit)


@router.get("/publishes/{publish_id}", response_model=PublishOut)
async def get_publish(publish_id: str, db: AsyncSession = Depends(get_db)) -> PublishOut:
    pub = await db.get(Publish, publish_id, populate_existing=True)
    if pub is None:
        raise NotFoundError(f"No publish {publish_id}.", code="publish_not_found")
    return await _publish_out(db, pub, True)


# --- licence table ----------------------------------------------------------------------------


@router.get("/licence-table", response_model=LicenceTableOut)
async def licence_table() -> LicenceTableOut:
    return LicenceTableOut(
        table="dw.licence-table",
        version=TABLE.version,
        permits_redistribution=sorted(TABLE.permits),
        classes=[c.value for c in LicenceClass],
    )
