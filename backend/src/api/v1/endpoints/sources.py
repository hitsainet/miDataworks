"""Sources REST (FR-001.1, 001.13, 001.21, 001.28, 001.34–001.36, 001.38; 001 FTDD section 5).

- Preview and import carry ``secret_write`` when an AGENT sends a token (P-11): the token is popped
  from the stored request, HMAC'd for its digest and kept only encrypted until the operator decides.
- An operator's token goes straight into the ephemeral store under the job (or preview) id and never
  into a job's parameters, a Celery argument or a log.
- Every agent annotation waits for ``source_annotate`` (S3-01). The approval binds to the current
  annotation of that kind (``expected_current_id``), and a stale one writes nothing.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ....core import ephemeral_secrets
from ....core.agent_origin import Actor, get_actor, requires_approval_when_agent, resolve_who
from ....core.celery_app import celery_app
from ....core.config import get_settings
from ....core.database import get_db, get_sync_db
from ....core.errors import AppError
from ....core.storage import staging_dir
from ....models.enums import TargetType, values
from ....models.source import Source
from ....models.source_enums import AnnotationKind, Redistribution, SourceKind, SourceState
from ....schemas.sources import (
    AnnotationIn,
    AnnotationOut,
    DeleteSourceIn,
    HfImportRequest,
    HfPreviewRequest,
    ImportAccepted,
    SourceFileOut,
    SourceList,
    SourceOut,
    SourceRowsPage,
    SourcesMeta,
    SourceSummary,
    UploadManifest,
)
from ....services.job_service import JobService, dispatch_queued
from ....services.sources import detection, preview_service, source_service, upload_service
from ._paging import Page, paging

router = APIRouter(prefix="/api/v1/sources", tags=["sources"])

_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def _dispatch() -> list[str]:
    with get_sync_db() as session:
        return dispatch_queued(session)


def _has_token(request_values: dict[str, Any], _db: AsyncSession) -> bool:
    return request_values["body"].access_token is not None


async def _confirm_bytes(db: AsyncSession) -> int:
    from ....services.app_setting_service import AppSettingService

    return await AppSettingService.get_int(db, "import_confirm_bytes")


async def _upload_cap(db: AsyncSession) -> int:
    from ....services.app_setting_service import AppSettingService

    return await AppSettingService.get_int(db, "upload_max_bytes")


# --------------------------------------------------------------------------------------------
# Serialisation (one serialiser for the list and the detail)
# --------------------------------------------------------------------------------------------


async def summary_of(db: AsyncSession, source: Source) -> SourceSummary:
    files = await source_service.files_of(db, source.id)
    detection = source.detection or {}
    return SourceSummary(
        id=source.id,
        kind=source.kind,
        state=source.state,
        display_name=source.display_name,
        repo_id=source.repo_id,
        config=source.config,
        split_selection=source.split_selection,
        requested_ref=source.requested_ref,
        resolved_commit=source.resolved_commit,
        content_hash=source.content_hash,
        licence_display=source.licence_display,
        licence_origin=source.licence_origin,
        gated=source.gated,
        token_tier=source.token_tier,
        rows=sum(f.rows for f in files),
        splits=[f.split for f in files],
        suggested_target=detection.get("suggested_target"),
        import_job_id=source.import_job_id,
        created_by=source.created_by,
        created_at=source.created_at,
        ready_at=source.ready_at,
    )


async def source_out(db: AsyncSession, source: Source) -> SourceOut:
    summary = await summary_of(db, source)
    files = await source_service.files_of(db, source.id)
    return SourceOut(
        **summary.model_dump(),
        files=[
            SourceFileOut(
                split=f.split,
                path=f.path,
                rows=f.rows,
                bytes=f.bytes,
                sha256=f.sha256,
                columns=list(f.columns),
                original_name=f.original_name,
                original_sha256=f.original_sha256,
            )
            for f in files
        ],
        licence=await source_service.current_licence_view(db, source),
        detection=await source_service.effective_detection(db, source),
        library_versions=dict(source.library_versions),
        error=source.error,
        deleted_by=source.deleted_by,
        deleted_at=source.deleted_at,
    )


# --------------------------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------------------------


@router.get("/meta", response_model=SourcesMeta)
async def sources_meta(db: AsyncSession = Depends(get_db)) -> SourcesMeta:
    return SourcesMeta(
        kinds=values(SourceKind),
        states=values(SourceState),
        annotation_kinds=values(AnnotationKind),
        redistribution=values(Redistribution),
        chat_formats=list(detection.CHAT_FORMATS),
        trl_types=list(detection.TRL_TYPES),
        target_types=values(TargetType),
        csv_defaults=dict(upload_service.CSV_DEFAULTS),
        limits={
            "upload_max_bytes": await _upload_cap(db),
            "import_confirm_bytes": await _confirm_bytes(db),
            "preview_sample_rows": preview_service.SAMPLE_LIMIT,
        },
    )


def send_preview(preview_id: str, request: dict[str, Any]) -> Any:
    """Enqueue ``preview_hf`` on ``default``; returns the Celery result handle."""
    return celery_app.send_task("midataworks.sources.preview_hf", args=[preview_id, request])


async def wait_for_preview(preview_id: str, request: dict[str, Any]) -> dict[str, Any]:
    """Wait up to ``PREVIEW_TIMEOUT_S`` for the preview (the stored token lives in workers).

    On timeout no revoke is attempted (a busy solo worker never reads it); the result simply
    expires from the backend after 300 s.
    """
    from celery.exceptions import TimeoutError as CeleryTimeout

    timeout = get_settings().preview_timeout_s

    def run() -> dict[str, Any]:
        value: dict[str, Any] = send_preview(preview_id, request).get(timeout=timeout)
        return value

    try:
        return await asyncio.to_thread(run)
    except CeleryTimeout:
        raise AppError(
            f"The preview took longer than {timeout:.0f} s. Try again, or import directly.",
            code="preview_timeout",
            status_code=504,
        ) from None


@router.post("/hf/preview")
@requires_approval_when_agent("secret_write", when=_has_token, secret_fields=("body.access_token",))
async def preview_hf(
    body: HfPreviewRequest,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> dict[str, Any]:
    """Preview before importing: commit, configs, splits, sample rows, licence and detection."""
    preview_id = f"preview_{uuid.uuid4().hex}"
    token = body.token()
    if token:
        ephemeral_secrets.put(preview_id, token)
    request = body.model_dump(exclude={"access_token"}) | {"token_supplied": token is not None}
    answer = await wait_for_preview(preview_id, request)
    if "error" in answer:
        error = answer["error"]
        raise AppError(
            error["message"],
            code=error["code"],
            status_code=int(error.get("status", 502)),
            details=error.get("details"),
        )
    preview: dict[str, Any] = answer["preview"]
    return preview


@router.post(
    "/hf",
    response_model=SourceOut | ImportAccepted,
    status_code=202,
    responses={200: {"model": SourceOut}, 202: {"model": ImportAccepted}},
)
@requires_approval_when_agent("secret_write", when=_has_token, secret_fields=("body.access_token",))
async def import_hf(
    body: HfImportRequest,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> Any:
    """Import at a pinned commit. A full SHA that is already imported answers at once (200)."""
    who = await resolve_who(actor, db)
    if body.revision and _FULL_SHA.fullmatch(body.revision):
        found = await source_service.find_live_hf(
            db, body.repo_id, body.config, body.split, body.revision
        )
        if found is not None and found.state == SourceState.READY:
            return JSONResponse(
                status_code=200, content=(await source_out(db, found)).model_dump(mode="json")
            )
        if found is not None and found.import_job_id:
            accepted = ImportAccepted(
                job_id=found.import_job_id, source_id=found.id, existing_job=True
            )
            return JSONResponse(status_code=202, content=accepted.model_dump(mode="json"))
    token = body.token()
    job = await JobService.create(
        db,
        kind="source_import",
        params={
            "mode": "hf",
            "repo_id": body.repo_id,
            "config": body.config,
            "split": body.split,
            "revision": body.revision,
            "confirm_large": body.confirm_large,
            "token_supplied": token is not None,
        },
        started_by=who.who,
        origin=who.origin,
    )
    if token:
        ephemeral_secrets.put(job.id, token)
    await run_in_threadpool(_dispatch)
    accepted = ImportAccepted(job_id=job.id)
    return JSONResponse(status_code=202, content=accepted.model_dump(mode="json"))


@router.post(
    "/uploads",
    response_model=SourceOut | ImportAccepted,
    status_code=202,
    responses={200: {"model": SourceOut}, 202: {"model": ImportAccepted}},
)
async def upload_files(
    files: list[UploadFile] = File(...),
    manifest: str = Form(...),
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> Any:
    """Upload Parquet, JSONL or CSV files as one source, each file a split (T-03)."""
    try:
        plan = UploadManifest.model_validate_json(manifest)
    except ValidationError as exc:
        raise AppError(
            "The upload manifest is not valid: list each file with its split.",
            code="upload_manifest_invalid",
            status_code=422,
            details={"errors": [e["msg"] for e in exc.errors()]},
        ) from None
    if sorted(e.name for e in plan.files) != sorted(f.filename or "" for f in files):
        raise AppError(
            "The manifest must name every uploaded file exactly once.",
            code="upload_manifest_invalid",
            status_code=422,
        )
    if len({e.split for e in plan.files}) != len(plan.files):
        raise AppError(
            "Give each file its own split name.", code="upload_manifest_invalid", status_code=422
        )
    who = await resolve_who(actor, db)
    cap = await _upload_cap(db)
    upload_dir = f"upload_{uuid.uuid4().hex}"
    directory = staging_dir() / upload_dir
    split_of = {e.name: e.split for e in plan.files}
    csv = plan.csv.model_dump() if plan.csv else None
    entries: list[dict[str, Any]] = []
    try:
        for index, upload in enumerate(files):
            name = upload.filename or f"file-{index}"
            staged = f"{index:03d}.bin"  # the server's name; the user's is data only
            if upload.size is not None and upload.size > cap:
                raise AppError(
                    f"{name} is over the {cap:,}-byte upload limit. Split it, or raise the limit "
                    "in Settings → Storage.",
                    code="upload_too_large",
                    status_code=413,
                    details={"file": name, "limit_bytes": cap},
                )
            received = await upload_service.receive_stream(upload, directory / staged, cap, name)
            fmt = upload_service.sniff_format(received.path)
            upload_service.check_extension(name, fmt)
            entries.append(
                {
                    "name": name,
                    "split": split_of[name],
                    "staged": staged,
                    "sha256": received.sha256,
                    "bytes": received.bytes,
                    "parse_options": upload_service.parse_options(fmt, csv),
                }
            )
    except BaseException:
        import shutil

        shutil.rmtree(directory, ignore_errors=True)
        raise
    digest = upload_service.content_hash(entries)
    existing = await source_service.find_by_content_hash(db, digest)
    if existing is not None:
        import shutil

        shutil.rmtree(directory, ignore_errors=True)
        if existing.state == SourceState.READY:
            return JSONResponse(
                status_code=200, content=(await source_out(db, existing)).model_dump(mode="json")
            )
        accepted = ImportAccepted(
            job_id=existing.import_job_id or "", source_id=existing.id, existing_job=True
        )
        return JSONResponse(status_code=202, content=accepted.model_dump(mode="json"))
    job = await JobService.create(
        db,
        kind="source_import",
        params={
            "mode": "upload",
            "upload_dir": upload_dir,
            "files": entries,
            "csv": csv,
            "display_name": plan.display_name or entries[0]["name"],
            "content_hash": digest,
        },
        started_by=who.who,
        origin=who.origin,
    )
    await run_in_threadpool(_dispatch)
    return JSONResponse(
        status_code=202, content=ImportAccepted(job_id=job.id).model_dump(mode="json")
    )


@router.get("", response_model=SourceList)
async def list_sources(
    kind: SourceKind | None = Query(None),
    state: SourceState | None = Query(None),
    page: Page = Depends(paging),
    db: AsyncSession = Depends(get_db),
) -> SourceList:
    query = select(Source)
    if kind:
        query = query.where(Source.kind == kind.value)
    if state:
        query = query.where(Source.state == state.value)
    else:
        query = query.where(Source.state != SourceState.DELETED)
    total = int((await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one())
    rows = (
        await db.execute(
            query.order_by(Source.created_at.desc()).offset(page.offset).limit(page.limit)
        )
    ).scalars()
    return SourceList(
        items=[await summary_of(db, r) for r in rows], total=total, page=page.page, limit=page.limit
    )


@router.get("/{source_id}", response_model=SourceOut)
async def get_source(source_id: str, db: AsyncSession = Depends(get_db)) -> SourceOut:
    return await source_out(db, await source_service.get_source_row(db, source_id))


@router.get("/{source_id}/rows", response_model=SourceRowsPage)
async def get_source_rows(
    source_id: str,
    split: str | None = Query(None, max_length=128),
    page: Page = Depends(paging),
    db: AsyncSession = Depends(get_db),
) -> SourceRowsPage:
    """A page of a ready source's rows (``limit`` at most 200), read with DuckDB from the
    recorded file paths, never a path from the request."""
    return SourceRowsPage(
        **await source_service.rows_page(db, source_id, split, page.page, page.limit)
    )


async def _annotation_facts(request_values: dict[str, Any], db: AsyncSession) -> dict[str, Any]:
    body = request_values["body"].model_dump(mode="json")
    facts = await source_service.annotation_card_facts(db, request_values["source_id"], body)
    return {"facts": facts, "expected_current_id": facts["expected_current_id"]}


def _annotation_summary(payload: dict[str, Any]) -> str:
    facts = payload["facts"]
    proposed = facts["proposed"]
    what = " ".join(p for p in (proposed["kind"], proposed.get("redistribution")) if p)
    line = f"Annotate {facts['display_name']}: {what}"
    return f"{line}. {facts['warning']}" if facts.get("warning") else line


@router.post(
    "/{source_id}/annotations",
    status_code=201,
    response_model=AnnotationOut,
    responses={202: {"description": "An agent's request waits for approval (source_annotate)"}},
)
@requires_approval_when_agent(
    "source_annotate", enrich=_annotation_facts, summary=_annotation_summary
)
async def annotate_source(
    source_id: str,
    body: AnnotationIn,
    # Set only by the stored approval (enrich below): the annotation the agent saw. Hidden from
    # the schema, and compared only while an approval executes.
    expected_current_id: str | None = Query(None, max_length=64, include_in_schema=False),
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> AnnotationOut:
    """Annotate a source's licence, terms or detection. An agent's request waits for approval."""
    who = await resolve_who(actor, db)
    approval = await source_service.approval_ref(db, actor.approval_id)
    row = await source_service.annotate(
        db,
        source_id,
        body.model_dump(mode="json"),
        who,
        approval=approval,
        expected_current_id=expected_current_id,
        check_current=actor.approval_id is not None,
    )
    return AnnotationOut(**source_service.annotation_dict(row))


@router.delete("/{source_id}", response_model=SourceOut)
async def delete_source(
    source_id: str,
    body: DeleteSourceIn,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> SourceOut:
    """Tombstone an unreferenced source and remove its files (not gated for agents, P-15)."""
    who = await resolve_who(actor, db)
    source = await source_service.delete(db, source_id, body.reason, who)
    return await source_out(db, source)
