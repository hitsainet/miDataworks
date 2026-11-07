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
from ....services import label_inputs, server_probe
from ....services.endpoint_resolver import resolve_async
from ....services.job_service import JobService
from ....services.label_run_engine import (
    RunStop,
    SampleSpec,
    sample_steering_state,
    score_sample,
)
from ....services.label_run_service import (
    _dispatch,
    _thresholds,
    _version_files,
    caller_for,
    refuse_paid_preview,
)
from ._paging import OptionalPage, optional_paging

router = APIRouter(prefix="/api/v1", tags=["labeling"])


@router.post("/endpoint-roles/{role}/test", response_model=EndpointTestResult)
async def test_endpoint(role: str, db: AsyncSession = Depends(get_db)) -> EndpointTestResult:
    resolved = await resolve_async(db, role)

    def _check() -> EndpointTestResult:
        with caller_for(
            resolved.base_url, resolved.api_key, key_origin=resolved.key_origin, transient_retries=0
        ) as caller:
            return server_probe.check(resolved, caller)

    return await run_in_threadpool(_check)


@router.post("/labeling/sample", response_model=SampleResult)
async def sample(
    body: SampleRequest,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> SampleResult:
    settings = get_settings()
    if body.rows > settings.label_sample_max_rows:
        raise UnprocessableError(
            f"Try at most {settings.label_sample_max_rows} rows on a sample; estimate the keep "
            "share for a larger one.",
            code="SAMPLE_TOO_LARGE",
        )
    files = await _version_files(db, body.input_version_id)
    resolved = await resolve_async(db, body.role)
    # Review R4 (017 FR-017.40): a preview on a paid role sends text and costs money.
    await refuse_paid_preview(db, resolved, body.rows, actor.origin)
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

    def _score() -> tuple[SampleSpec, list[dict[str, Any]]]:
        with caller_for(
            resolved.base_url, resolved.api_key, key_origin=resolved.key_origin, transient_retries=0
        ) as caller:
            kind = server_probe.detect_server(caller).kind
        spec = SampleSpec(
            role=body.role,
            protocol=resolved.protocol,
            base_url=resolved.base_url,
            model_id=resolved.model_id,
            api_key=resolved.api_key,
            key_origin=resolved.key_origin,
            template_body=template_body,
            rubric_body=rubric_body,
            question=body.question,
            threshold_positive=body.threshold_positive,
            threshold_negative=body.threshold_negative,
            min_top_probability=body.min_top_probability,
            label_set=list(label_set),
            server_kind=kind,
        )
        return spec, score_sample(spec, rows, deadline_s=settings.label_sample_timeout_seconds)

    try:
        spec, scored = await run_in_threadpool(_score)
    except RunStop as stop:
        raise AppError(stop.message, code=stop.code, status_code=409) from None
    except EndpointCallError as exc:
        raise AppError(exc.message, code=exc.code, status_code=502) from None
    # The same recording a label run makes (2026-10-18: the preview always said "unsteered
    # (scoring mode)" while runs recorded miLLM's ``X-miLLM-Steering: none``).
    steering = sample_steering_state(spec, scored)
    return SampleResult(
        rows=[
            SampleRow.model_validate({k: v for k, v in r.items() if k != "steering_state"})
            for r in scored
        ],
        model=resolved.model_id,
        server_kind=spec.server_kind,
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
    # Review R4 (017 FR-017.40): the rows the worker will score (``preview_job``).
    preview_rows = min(
        body.sample_rows or settings.keep_share_sample_rows, settings.keep_share_max_rows
    )
    await refuse_paid_preview(db, resolved, preview_rows, who.origin)
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


def _dataset_name(ref: Any) -> str | None:
    if not isinstance(ref, dict) or not ref.get("hf_id"):
        return None
    split = ref.get("split")
    return f"{ref['hf_id']} ({split})" if split else str(ref["hf_id"])


def probe_facts(definition: dict[str, Any]) -> dict[str, Any]:
    """When and on what miStudio trained a probe, and its AUROC, from its definition."""
    provenance = definition.get("provenance") or {}
    evaluations = [
        e
        for e in (definition.get("evidence") or {}).get("evaluations") or []
        if isinstance(e, dict)
    ]
    chosen = next(
        (e for e in evaluations if e.get("distribution") == "in_distribution"),
        evaluations[0] if evaluations else None,
    )
    auroc = chosen.get("auroc") if chosen else None
    return {
        "trained_at": str(provenance["created_at"]) if provenance.get("created_at") else None,
        "train_dataset": _dataset_name(provenance.get("train_dataset")),
        "mistudio_run_id": str(provenance["run_id"]) if provenance.get("run_id") else None,
        "auroc": float(auroc) if isinstance(auroc, int | float) else None,
        "auroc_dataset": _dataset_name(chosen.get("dataset")) if chosen else None,
    }


@router.get("/labeling/probes", response_model=ProbeList)
async def list_probes(paging: OptionalPage = Depends(optional_paging)) -> ProbeList:
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
            rows = list(response.body["data"])
            # Each probe's definition says when and on what miStudio trained it, and how well it
            # did; read best-effort (a probe whose detail does not answer keeps its list facts).
            for row in rows:
                try:
                    detail = caller.raw("GET", f"/api/probes/{row['id']}")
                except EndpointCallError:
                    continue
                if detail.status == 200 and isinstance(detail.body, dict):
                    data = detail.body.get("data")
                    if isinstance(data, dict) and isinstance(data.get("definition"), dict):
                        row["_facts"] = probe_facts(data["definition"])
            return resident, rows

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
            # Served by miLLM since 2026-10-01; an older miLLM sends neither, which reads as
            # "not stated" (an empty mapping, no concept), never as a guessed label.
            label_mapping={str(k): str(v) for k, v in (r.get("label_mapping") or {}).items()},
            concept=r.get("concept") if isinstance(r.get("concept"), str) else None,
            created_at=str(r["created_at"]) if r.get("created_at") else None,
            target_fpr=(
                r.get("target_fpr") if isinstance(r.get("target_fpr"), int | float) else None
            ),
            **r.get("_facts", {}),
        )
        for r in rows
    ]
    page_items, meta = paging.cut(items)
    return ProbeList(items=page_items, resident_model=resident, millm_base_url=base_url, **meta)
