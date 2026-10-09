"""Feature 007's routes (FTDD 007 section 5.1; FTID 007 section 5).

Thin handlers: parse, call the service, map typed exceptions to the envelope. No route takes a
secret, loads a model or adds an approval action (FR-007.30, FR-007.31): an agent's start runs at
once and returns ``202`` with its job; a judge run over the generated rows inherits 005's
``agent_label_rows`` gate. Cancel is also reachable through Foundation's job cancel.

Importing this module installs feature 007 into 002's binding resolvers, 002's delete guard and
006's metric registry (an ASGI test client runs no lifespan).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.agent_origin import Actor, get_actor, resolve_who
from ....core.database import get_db, get_sync_db
from ....core.errors import NotFoundError
from ....core.job_kinds import get_job_kind
from ....models.generation import GenerationPair, GenerationRecord
from ....schemas.generation import (
    CandidateBuildRequest,
    CompareOut,
    CompareRequest,
    DiversityAccepted,
    DiversityReportOut,
    DiversityRequest,
    GenerationRunCreate,
    GenerationRunList,
    GenerationRunOut,
    IndependenceOut,
    IndependenceRequest,
    PairOut,
    PairPage,
    PlanOut,
    PreviewOut,
    PreviewRequest,
    RecordOut,
    RecordPage,
    TemplateClone,
    TemplateCreate,
    TemplateList,
    TemplateOut,
)
from ....services.generation import diversity_service, record_store, template_service
from ....services.generation import install as generation_install
from ....services.generation import run_service as svc
from ....services.labeling_ports import register_bindings
from ._builds import respond as build_respond
from ._paging import Page, paging

router = APIRouter(prefix="/api/v1", tags=["generation"])

register_bindings()
generation_install.install()


# --- templates ------------------------------------------------------------------------------


@router.get("/generation-templates", response_model=TemplateList)
async def list_templates(
    kind: str | None = Query(None, pattern="^(expand|respond)$"),
    page: Page = Depends(paging),
    db: AsyncSession = Depends(get_db),
) -> TemplateList:
    rows, total = await template_service.list_templates(
        db, kind=kind, page=page.page, limit=page.limit
    )
    return TemplateList(
        items=[template_service.template_out(r) for r in rows],
        total=total,
        page=page.page,
        limit=page.limit,
    )


@router.post("/generation-templates", response_model=TemplateOut, status_code=201)
async def create_template(
    body: TemplateCreate, db: AsyncSession = Depends(get_db), actor: Actor = Depends(get_actor)
) -> TemplateOut:
    who = await resolve_who(actor, db)
    row = await template_service.create(
        db, who, name=body.name, kind=body.kind, description=body.description, body=body.body
    )
    return template_service.template_out(row)


@router.get("/generation-templates/{template_id}", response_model=TemplateOut)
async def get_template(template_id: str, db: AsyncSession = Depends(get_db)) -> TemplateOut:
    await template_service.ensure_builtins(db)
    return template_service.template_out(await template_service.get(db, template_id))


@router.post(
    "/generation-templates/{template_id}/clone", response_model=TemplateOut, status_code=201
)
async def clone_template(
    template_id: str,
    body: TemplateClone,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> TemplateOut:
    who = await resolve_who(actor, db)
    row = await template_service.clone(
        db, who, template_id, body=body.body, description=body.description
    )
    return template_service.template_out(row)


# --- runs -----------------------------------------------------------------------------------


@router.post("/generation-runs/plan", response_model=PlanOut)
async def plan_run(
    body: GenerationRunCreate, db: AsyncSession = Depends(get_db), actor: Actor = Depends(get_actor)
) -> PlanOut:
    """A dry run of ``POST /generation-runs``: every guard, counts and identities; writes nothing."""
    return (await svc.plan(db, body, actor.origin)).out


@router.post(
    "/generation-runs",
    response_model=GenerationRunOut,
    status_code=202,
    responses={202: {"description": "The run's first job is queued"}},
)
async def start_run(
    body: GenerationRunCreate, db: AsyncSession = Depends(get_db), actor: Actor = Depends(get_actor)
) -> JSONResponse:
    who = await resolve_who(actor, db)  # NO_IDENTITY
    planned = await svc.plan(db, body, who.origin)
    run = await svc.start(db, body, planned, who)
    out = await svc.run_out(db, run)
    return JSONResponse(status_code=202, content=out.model_dump(mode="json"))


@router.get("/generation-runs", response_model=GenerationRunList)
async def list_runs(
    input_version_id: str | None = Query(None, max_length=64),
    state: str | None = Query(None, max_length=24),
    mode: str | None = Query(None, pattern="^(standard|steered_pairs|minimal_pairs)$"),
    page: Page = Depends(paging),
    db: AsyncSession = Depends(get_db),
) -> GenerationRunList:
    rows, total = await svc.list_runs(
        db,
        input_version_id=input_version_id,
        state=state,
        mode=mode,
        page=page.page,
        limit=page.limit,
    )
    return GenerationRunList(
        items=[await svc.run_out(db, r) for r in rows],
        total=total,
        page=page.page,
        limit=page.limit,
    )


@router.post("/generation-runs/preview", response_model=PreviewOut)
async def preview(body: PreviewRequest, db: AsyncSession = Depends(get_db)) -> PreviewOut:
    """≤ 5 prompts, ≤ 60 s, no lease, refuse-load; writes no row, no file and no job."""
    return await svc.preview(db, body)


@router.post("/generation-runs/independence-check", response_model=IndependenceOut)
async def independence_check(
    body: IndependenceRequest, db: AsyncSession = Depends(get_db)
) -> IndependenceOut:
    return await svc.independence(db, body)


@router.post("/steering-settings/compare", response_model=CompareOut)
async def compare_settings(body: CompareRequest, db: AsyncSession = Depends(get_db)) -> CompareOut:
    return await svc.compare(db, body)


@router.get("/generation-runs/{run_id}", response_model=GenerationRunOut)
async def get_run(run_id: str, db: AsyncSession = Depends(get_db)) -> GenerationRunOut:
    return await svc.run_out(db, await svc.get_run(db, run_id))


def _texts(run_id: str, wanted: set[tuple[str, int]]) -> dict[tuple[str, int], Any]:
    with get_sync_db() as session:
        return record_store.texts_for(session, run_id, wanted)


@router.get("/generation-runs/{run_id}/records", response_model=RecordPage)
async def get_records(
    run_id: str,
    outcome: str | None = Query(None, pattern="^(generated|discarded|skipped)$"),
    stage: str | None = Query(None, pattern="^(expand|respond)$"),
    page: Page = Depends(paging),
    db: AsyncSession = Depends(get_db),
) -> RecordPage:
    await svc.get_run(db, run_id)
    query = select(GenerationRecord).where(GenerationRecord.run_id == run_id)
    if outcome:
        query = query.where(GenerationRecord.outcome == outcome)
    if stage:
        query = query.where(GenerationRecord.stage == stage)
    total = int((await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one())
    rows = list(
        (
            await db.execute(
                query.order_by(GenerationRecord.stage, GenerationRecord.record_index)
                .offset(page.offset)
                .limit(page.limit)
            )
        ).scalars()
    )
    texts = await run_in_threadpool(_texts, run_id, {(r.stage, int(r.record_index)) for r in rows})
    items = []
    for r in rows:
        prompt, text = texts.get((r.stage, int(r.record_index)), (None, None))
        items.append(
            RecordOut(
                stage=r.stage,
                record_index=int(r.record_index),
                seed_position=r.seed_position,
                row_key=r.row_key,
                seed_row_key=r.seed_row_key,
                prompt_row_key=r.prompt_row_key,
                response_index=r.response_index,
                side=r.side,
                template_id=r.template_id,
                model_id=r.model_id,
                model_revision=r.model_revision,
                requested_set_hash=r.requested_set_hash,
                reported_steering=r.reported_steering,
                steering_check=r.steering_check,
                check_reasons=list(r.check_reasons),
                seed_sent=r.seed_sent,
                seed_confirmed=r.seed_confirmed,
                latency_ms=r.latency_ms,
                finish_reason=r.finish_reason,
                outcome=r.outcome,
                reason_code=r.reason_code,
                chunk_index=r.chunk_index,
                prompt=prompt,
                text=text,
            )
        )
    return RecordPage(items=items, total=total, page=page.page, limit=page.limit)


@router.get("/generation-runs/{run_id}/pairs", response_model=PairPage)
async def get_pairs(
    run_id: str, page: Page = Depends(paging), db: AsyncSession = Depends(get_db)
) -> PairPage:
    await svc.get_run(db, run_id)
    query = select(GenerationPair).where(GenerationPair.run_id == run_id)
    total = int((await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one())
    rows = list(
        (
            await db.execute(
                query.order_by(GenerationPair.chunk_index, GenerationPair.record_index_a)
                .offset(page.offset)
                .limit(page.limit)
            )
        ).scalars()
    )
    wanted = {("respond", int(p.record_index_a)) for p in rows} | {
        ("respond", int(p.record_index_b)) for p in rows
    }
    texts = await run_in_threadpool(_texts, run_id, wanted)
    headers: dict[int, str | None] = {}
    if rows:
        for rec in (
            await db.execute(
                select(GenerationRecord).where(
                    GenerationRecord.run_id == run_id,
                    GenerationRecord.stage == "respond",
                    GenerationRecord.record_index.in_([i for _, i in wanted]),
                )
            )
        ).scalars():
            headers[int(rec.record_index)] = rec.reported_steering
    items = []
    for p in rows:
        prompt_a, text_a = texts.get(("respond", int(p.record_index_a)), (None, None))
        _, text_b = texts.get(("respond", int(p.record_index_b)), (None, None))
        items.append(
            PairOut(
                prompt_row_key=p.prompt_row_key,
                pair_index=p.pair_index,
                record_index_a=int(p.record_index_a),
                record_index_b=int(p.record_index_b),
                chosen_side=p.chosen_side,
                shared_seed=int(p.shared_seed),
                prompt=prompt_a,
                text_a=text_a,
                text_b=text_b,
                steering_a=headers.get(int(p.record_index_a)),
                steering_b=headers.get(int(p.record_index_b)),
            )
        )
    return PairPage(items=items, total=total, page=page.page, limit=page.limit)


@router.post("/generation-runs/{run_id}/cancel", response_model=GenerationRunOut)
async def cancel_run(run_id: str, db: AsyncSession = Depends(get_db)) -> GenerationRunOut:
    return await svc.run_out(db, await svc.cancel(db, run_id))


@router.post("/generation-runs/{run_id}/resume", response_model=GenerationRunOut)
async def resume_run(
    run_id: str, db: AsyncSession = Depends(get_db), actor: Actor = Depends(get_actor)
) -> GenerationRunOut:
    who = await resolve_who(actor, db)
    return await svc.run_out(db, await svc.resume(db, run_id, who))


@router.post("/generation-runs/{run_id}/candidate-build", status_code=202)
async def candidate_build(
    run_id: str,
    body: CandidateBuildRequest,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> JSONResponse:
    """Build candidate version C through 002 (``200`` existing version or ``202`` with the job)."""
    who = await resolve_who(actor, db)
    request = await svc.candidate_request(db, run_id, who, body.seed)
    return await build_respond(db, actor, request)


# --- diversity ------------------------------------------------------------------------------


def _report_out(row: Any) -> DiversityReportOut:
    return DiversityReportOut(
        id=row.id,
        version_id=str(row.version_id),
        reference_version_id=str(row.reference_version_id) if row.reference_version_id else None,
        column=row.column,
        method_hash=row.method_hash,
        method=row.method,
        splits=list(row.splits),
        sample_size=row.sample_size,
        seed=int(row.seed),
        embedding_identity=row.embedding_identity,
        clustering=row.clustering,
        figures=row.figures,
        checks=list(row.checks),
        verdict=row.verdict,
        reason=row.reason,
        job_id=row.job_id,
        created_by=row.created_by,
        created_by_origin=row.created_by_origin,
        created_at=row.created_at,
    )


def _latest(version_id: str, column: str | None) -> tuple[Any, str | None]:
    with get_sync_db() as session:
        row = diversity_service.latest(session, version_id, column)
        if row is not None:
            session.expunge(row)
            return row, None
        job = (
            diversity_service.live_job(session, version_id, column) if column is not None else None
        )
        return None, job


@router.get("/versions/{version_id}/diversity", response_model=DiversityReportOut)
async def get_diversity(
    version_id: str, column: str | None = Query(None, max_length=200)
) -> DiversityReportOut:
    row, job = await run_in_threadpool(_latest, version_id, column)
    if row is None:
        raise NotFoundError(
            "No diversity report for this version"
            + (f" and column {column!r}" if column else "")
            + (f"; job {job} is computing one." if job else "; start one with POST."),
            code="DIVERSITY_REPORT_NOT_FOUND",
            details={"job_id": job},
        )
    return _report_out(row)


def _request(version_id: str, column: str | None, who: str, origin: str) -> DiversityAccepted:
    from ....services.job_service import dispatch_queued

    with get_sync_db() as session:
        version, chosen = diversity_service.plan_request(session, version_id, column)
        job_id = diversity_service.create_job(session, version.id, chosen, who, origin)
        dispatch_queued(session)
        return DiversityAccepted(
            job_id=job_id,
            room=get_job_kind("diversity_report").room(job_id),
            column=chosen,
        )


@router.post("/versions/{version_id}/diversity", response_model=DiversityAccepted, status_code=202)
async def request_diversity(
    version_id: str,
    body: DiversityRequest,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> DiversityAccepted:
    """Start a report job (``DIVERSITY_INCOMPARABLE`` before any job when the sides differ)."""
    who = await resolve_who(actor, db)
    return await run_in_threadpool(_request, version_id, body.column, who.who, who.origin)
