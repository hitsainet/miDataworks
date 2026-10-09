"""Endpoint test, "Try it on a sample" and the keep-share estimate (FR-005.9, FR-005.19, FR-005.20).

- ``POST /api/v1/endpoint-roles/{role}/test`` — reachable, model listed, protocol works, miLLM
  with its loaded model, lease state and queue depth. Never a key or a lease ID in the answer.
- ``POST /api/v1/labeling/sample`` — at most ``LABEL_SAMPLE_MAX_ROWS`` rows, synchronous,
  ``X-miLLM-Load-Policy: refuse``, NO lease, writes nothing.
- ``POST /api/v1/labeling/keep-share`` — a ``label_preview`` job on a reservoir sample (no lease);
  ``GET …/{job_id}`` reads share, interval and sample size from the job record.
- ``GET /api/v1/labeling/probes`` — the probes imported in miLLM, for a probe-verdict run (009).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession

from ....clients.endpoint_errors import EndpointCallError
from ....clients.labelers.factory import TemplateModelMismatch, check_binding, parse_template
from ....core.agent_origin import Actor, get_actor, resolve_who
from ....core.config import get_settings
from ....core.database import get_db
from ....core.errors import AppError, ConflictError, NotFoundError, UnprocessableError
from ....core.job_kinds import get_job_kind
from ....models.decision_template import DecisionTemplate
from ....models.rubric import Rubric
from ....schemas.labeling import (
    EndpointTestResult,
    KeepShareAccepted,
    KeepShareRequest,
    KeepShareResult,
    ProbeList,
    ProbeListItem,
    RubricBody,
    SampleRequest,
    SampleResult,
    SampleRow,
)
from ....services import label_inputs, labeling_rules, server_probe
from ....services.endpoint_resolver import resolve_async
from ....services.job_service import JobService
from ....services.label_run_engine import RunStop, SampleSpec, score_sample
from ....services.label_run_service import _dispatch, _thresholds, _version_files, caller_for

router = APIRouter(prefix="/api/v1", tags=["labeling"])


@router.post("/endpoint-roles/{role}/test", response_model=EndpointTestResult)
async def test_endpoint(role: str, db: AsyncSession = Depends(get_db)) -> EndpointTestResult:
    resolved = await resolve_async(db, role)

    def _check() -> EndpointTestResult:
        with caller_for(resolved.base_url, resolved.api_key, transient_retries=0) as caller:
            return server_probe.check(resolved, caller)

    return await run_in_threadpool(_check)


@router.post("/labeling/sample", response_model=SampleResult)
async def sample(body: SampleRequest, db: AsyncSession = Depends(get_db)) -> SampleResult:
    settings = get_settings()
    if body.rows > settings.label_sample_max_rows:
        raise UnprocessableError(
            f"Try at most {settings.label_sample_max_rows} rows on a sample; estimate the keep "
            "share for a larger one.",
            code="SAMPLE_TOO_LARGE",
        )
    files = await _version_files(db, body.input_version_id)
    resolved = await resolve_async(db, body.role)
    template_body = rubric_body = None
    if body.role == "classifier":
        template = await db.get(DecisionTemplate, body.template_id or "")
        if template is None:
            raise NotFoundError("Choose a decision template.", code="TEMPLATE_NOT_FOUND")
        parsed = parse_template(template.body)
        try:
            check_binding(parsed, resolved.model_id)
        except TemplateModelMismatch as exc:
            raise ConflictError(str(exc), code=exc.code) from None
        template_body, label_set, fields = (
            template.body,
            list(parsed.label_set),
            parsed.input_fields,
        )
        if body.threshold_positive is not None or body.threshold_negative is not None:
            _thresholds(
                body.threshold_positive,
                body.threshold_negative,
                body.min_top_probability,
                label_set,
            )
    else:
        rubric = await db.get(Rubric, body.rubric_id or "")
        if rubric is None:
            raise NotFoundError("Choose a rubric.", code="RUBRIC_NOT_FOUND")
        rubric_parsed = RubricBody.model_validate(rubric.body)
        rubric_body, label_set, fields = (
            rubric.body,
            rubric_parsed.allowed_verdicts,
            (rubric_parsed.input_fields),
        )
    await run_in_threadpool(label_inputs.check_field_map, files, body.field_map, fields)
    row_filter = body.row_filter.normalised() if body.row_filter else None
    rows = await run_in_threadpool(
        label_inputs.sample_rows, files, body.field_map, row_filter, body.rows, body.seed
    )

    def _score() -> tuple[str, list[dict[str, object]]]:
        with caller_for(resolved.base_url, resolved.api_key, transient_retries=0) as caller:
            kind = server_probe.detect_server(caller).kind
        spec = SampleSpec(
            role=body.role,
            protocol=resolved.protocol,
            base_url=resolved.base_url,
            model_id=resolved.model_id,
            api_key=resolved.api_key,
            template_body=template_body,
            rubric_body=rubric_body,
            question=body.question,
            threshold_positive=body.threshold_positive,
            threshold_negative=body.threshold_negative,
            min_top_probability=body.min_top_probability,
            label_set=list(label_set),
            server_kind=kind,
        )
        return kind, score_sample(spec, rows, deadline_s=settings.label_sample_timeout_seconds)

    try:
        kind, scored = await run_in_threadpool(_score)
    except RunStop as stop:
        raise AppError(stop.message, code=stop.code, status_code=409) from None
    except EndpointCallError as exc:
        raise AppError(exc.message, code=exc.code, status_code=502) from None
    steering = (
        labeling_rules.UNSTEERED_SCORING
        if kind == "millm" and body.role == "classifier"
        else labeling_rules.NOT_REPORTED
    )
    return SampleResult(
        rows=[SampleRow.model_validate(r) for r in scored],
        model=resolved.model_id,
        server_kind=kind,
        steering_state=steering,
    )


@router.post("/labeling/keep-share", response_model=KeepShareAccepted, status_code=202)
async def start_keep_share(
    body: KeepShareRequest,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> KeepShareAccepted:
    settings = get_settings()
    if body.sample_rows is not None and body.sample_rows > settings.keep_share_max_rows:
        raise UnprocessableError(
            f"A keep-share estimate scores at most {settings.keep_share_max_rows} rows.",
            code="SAMPLE_TOO_LARGE",
        )
    who = await resolve_who(actor, db)
    files = await _version_files(db, body.input_version_id)
    resolved = await resolve_async(db, "classifier")
    template = await db.get(DecisionTemplate, body.template_id)
    if template is None:
        raise NotFoundError(f"No decision template {body.template_id}.", code="TEMPLATE_NOT_FOUND")
    parsed = parse_template(template.body)
    try:
        check_binding(parsed, resolved.model_id)
    except TemplateModelMismatch as exc:
        raise ConflictError(str(exc), code=exc.code) from None
    _thresholds(
        body.threshold_positive,
        body.threshold_negative,
        body.min_top_probability,
        list(parsed.label_set),
    )
    await run_in_threadpool(
        label_inputs.check_field_map, files, body.field_map, parsed.input_fields
    )
    params = body.model_dump(mode="json")
    if body.row_filter is not None:
        params["row_filter"] = body.row_filter.normalised()
    job = await JobService.create(
        db, kind="label_preview", params=params, started_by=who.who, origin=who.origin
    )
    await run_in_threadpool(_dispatch)
    return KeepShareAccepted(job_id=job.id, room=get_job_kind("label_preview").room(job.id))


@router.get("/labeling/keep-share/{job_id}", response_model=KeepShareResult)
async def get_keep_share(job_id: str, db: AsyncSession = Depends(get_db)) -> KeepShareResult:
    job = await JobService.get(db, job_id)
    if job.kind != "label_preview":
        raise NotFoundError(f"{job_id} is not a keep-share estimate.", code="JOB_NOT_FOUND")
    result = job.result or {}
    return KeepShareResult(
        job_id=job.id,
        status=job.status,
        share=result.get("share"),
        lo=result.get("lo"),
        hi=result.get("hi"),
        n=result.get("n"),
        seed=result.get("seed"),
        probabilities=result.get("probabilities"),
        error=job.error,
    )


# --- the probes a probe-verdict run can use (009; operator decision 2026-10-07) ---------------


@router.get("/labeling/probes", response_model=ProbeList)
async def list_probes() -> ProbeList:
    """The probes imported in miLLM (``GET /api/probes``), with what each was fitted on, so the
    Labeling screen and an agent can choose one. Read-only: nothing in miLLM changes."""
    from ....services.detector_sets import probe_protocol as pp

    try:
        base_url = pp.millm_base_url()
    except pp.ProbeRefused as refused:
        raise refused.as_app_error() from None

    def _read() -> tuple[str | None, list[dict[str, Any]]]:
        with caller_for(base_url, None, transient_retries=0) as caller:
            info = server_probe.detect_server(caller)
            if not info.is_millm:
                raise AppError(
                    f"MILLM_BASE_URL ({base_url}) answers as a {info.kind} server, not miLLM.",
                    code="PROBE_ENDPOINT_NOT_MILLM",
                    status_code=409,
                )
            response = caller.raw("GET", "/api/probes")
            if response.status != 200 or not isinstance(response.body, dict):
                raise AppError(
                    f"miLLM answered {response.status} listing probes.",
                    code="PROBE_LIST_FAILED",
                    status_code=502,
                )
            resident = (info.resident.repo_id or info.resident.name) if info.resident else None
            return resident, list(response.body["data"])

    try:
        resident, rows = await run_in_threadpool(_read)
    except EndpointCallError as exc:
        raise AppError(exc.message, code=exc.code, status_code=502) from None
    items = [
        ProbeListItem(
            probe_id=str(r["id"]),
            name=str(r["name"]),
            hf_id=str(r["hf_id"]),
            layer=int(r["layer"]),
            scope=str(r["scope"]),
            threshold=r["threshold"],
            threshold_revision=int(r["threshold_revision"]),
            window_thresholds={str(k): float(v) for k, v in (r["window_thresholds"] or {}).items()},
            rung=r["rung"],
            rung_language=r["rung_language"],
            armed=bool(r["armed"]),
            fits_resident_model=(resident == r["hf_id"]) if resident is not None else None,
        )
        for r in rows
    ]
    return ProbeList(items=items, resident_model=resident, millm_base_url=base_url)
