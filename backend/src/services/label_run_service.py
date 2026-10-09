"""Label runs: plan, start, resume, cancel, re-derive, aggregate, read (FR-005.19 – FR-005.36).

**The plan** (FTDD 005 section 2.2, step 1) resolves the role, loads the template or rubric,
checks the template–model binding (FR-005.13), counts the input rows after ``row_filter``
(FR-005.53), probes the server for its kind, resident model and revision, builds the labeler
identity and fingerprint, counts reusable rows (FR-005.29; reuse needs a REPORTED revision on both
sides), runs every preflight check (FR-005.54), and reads the agent window (P-07).

**What P-07 counts** is ``rows_to_score`` — input rows minus reused cached labels — summed per
input version over 24 hours in the shared ledger (``agent_label_ledger``), the SAME budget recipe
builds that label rows draw on (FR-002.50, S3-02). A resume re-sends no counted row. The decision is
``labeling_rules.approval_needed``, called once, in :func:`plan` (the route's approval predicate
reads the plan, so the REST gate and the dry-run plan cannot disagree).

``LabelRun.state`` is written only here and by the engine through :func:`set_state`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ..clients.endpoint_caller import EndpointCaller
from ..clients.endpoint_errors import EndpointCallError
from ..clients.labelers.factory import TemplateModelMismatch, check_binding, parse_template
from ..core.agent_origin import Actor, Who
from ..core.clock import utc_now
from ..core.config import get_settings
from ..core.errors import AppError, ConflictError, NotFoundError, UnprocessableError
from ..core.ids import new_id
from ..core.job_kinds import get_job_kind
from ..models.approval import Approval
from ..models.decision_template import DecisionTemplate
from ..models.enums import VersionState
from ..models.job import TERMINAL_STATUSES, Job
from ..models.label import Label
from ..models.label_run import (
    RESUMABLE_RUN_STATES,
    TERMINAL_RUN_STATES,
    LabelRun,
    LabelRunJob,
)
from ..models.rubric import Rubric
from ..schemas.labeling import (
    LabelOut,
    LabelPage,
    LabelRunOut,
    LabelRunStart,
    Plan,
    RubricBody,
)
from . import agent_label_ledger, label_inputs, labeling_rules, server_probe
from .decision_template_service import check_rubric_renders
from .endpoint_resolver import ResolvedRoleEndpoint, resolve_async
from .label_run_preflight import PreflightContext, PreflightRefused, run_checks
from .version_read_service import get_version_row

logger = logging.getLogger(__name__)

#: Outcomes whose labels are never reused (FTID 005 section 4: they hold no probability).
NOT_REUSABLE = ("skipped", "parse_failure")
#: Row keys per reuse-count query (distinct keys, so the batch counts add up).
REUSE_COUNT_BATCH = 10_000


def caller_for(base_url: str, api_key: str | None, **overrides: Any) -> EndpointCaller:
    settings = get_settings()
    values: dict[str, Any] = {
        "timeout_s": settings.endpoint_http_timeout_seconds,
        "backoff_cap_s": settings.label_backoff_cap_seconds,
        "transient_retries": settings.label_transient_retries,
    }
    values.update(overrides)
    return EndpointCaller(base_url, api_key, **values)


# --- the plan ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanResult:
    plan: Plan
    resolved: ResolvedRoleEndpoint
    template: DecisionTemplate | None
    rubric: Rubric | None
    template_ref: str
    label_set: list[str]
    sampling: dict[str, Any]
    structured_output: str
    packing: str
    row_filter: dict[str, str] | None
    server: server_probe.ServerInfo
    required_model_id: str | None


def _thresholds(
    positive: float | None, negative: float | None, min_top: float | None, label_set: list[str]
) -> None:
    """T-18 / T-19, as ``THRESHOLDS_INVALID`` (422) naming the field."""
    if len(label_set) == 2:
        try:
            labeling_rules.validate_thresholds(positive, negative)
        except labeling_rules.ThresholdsInvalid as exc:
            raise UnprocessableError(
                str(exc), code="THRESHOLDS_INVALID", details={"field": exc.field}
            ) from None
    elif min_top is None:
        raise UnprocessableError(
            "A label set of more than two labels needs 'min_top_probability' (T-18).",
            code="THRESHOLDS_INVALID",
            details={"field": "min_top_probability"},
        )


async def _version_files(db: AsyncSession, version_id: str) -> list[Any]:
    version = await get_version_row(db, version_id)
    if version.state != VersionState.COMPLETED:
        raise ConflictError(
            f"Version {version_id} is {version.state}; only a completed version can be labeled.",
            code="VERSION_NOT_LABELABLE",
        )
    return label_inputs.version_files(version.splits)


def _probe(resolved: ResolvedRoleEndpoint) -> server_probe.ServerInfo:
    with caller_for(resolved.base_url, resolved.api_key, transient_retries=0) as caller:
        return server_probe.detect_server(caller)


async def _active_model_jobs(db: AsyncSession, model: str) -> bool:
    """Is a miDataworks job running or queued on ``model``? (Then its lease covers a resident
    model and a job for another model WAITS in Foundation's queue rather than being refused.)"""
    found = (
        await db.execute(
            select(func.count())
            .select_from(Job)
            .where(Job.required_model_id == model, Job.status.in_(("queued", "running")))
        )
    ).scalar_one()
    return bool(found)


async def reusable_rows(
    db: AsyncSession,
    fingerprint: str,
    revision: str | None,
    keys: list[str],
    exclude_run: str | None = None,
) -> int:
    """Rows of this input already labeled under ``fingerprint`` by a run that reported its
    revision (FR-005.29, P-13)."""
    if not keys or not labeling_rules.reuse_allowed(revision, revision):
        return 0
    total = 0
    for start in range(0, len(keys), REUSE_COUNT_BATCH):  # asyncpg caps bind parameters
        query = (
            select(func.count(func.distinct(Label.row_key)))
            .select_from(Label)
            .join(LabelRun, LabelRun.id == Label.label_run_id)
            .where(
                Label.labeler_fingerprint == fingerprint,
                LabelRun.revision_reported.is_(True),
                Label.outcome.not_in(NOT_REUSABLE),
                Label.row_key.in_(keys[start : start + REUSE_COUNT_BATCH]),
            )
        )
        if exclude_run is not None:
            query = query.where(Label.label_run_id != exclude_run)
        total += int((await db.execute(query)).scalar_one())
    return total


async def plan(db: AsyncSession, req: LabelRunStart, origin: str) -> PlanResult:
    """The plan of a start, with the rows it covers beside the keys it scores (finding 2)."""
    files = await _version_files(db, req.input_version_id)
    result = await _plan_by_role(db, req, origin, files)
    covered = await run_in_threadpool(label_inputs.coverage, files, result.row_filter)
    if covered["row_keys"] != result.plan.rows_total:
        # The same files and filter counted twice; a disagreement is a defect, never a number.
        raise AppError(
            f"The plan counted {result.plan.rows_total} row keys and the coverage "
            f"{covered['row_keys']}; refusing rather than recording either.",
            code="ROW_COUNT_MISMATCH",
            status_code=500,
        )
    version = await get_version_row(db, req.input_version_id)
    disagree = [
        w for w in version.warnings or [] if w.get("code") == "duplicate_metadata_disagrees"
    ]
    result.plan.row_coverage = {**covered, "copies_disagree": disagree[0] if disagree else None}
    return result


async def _plan_by_role(
    db: AsyncSession, req: LabelRunStart, origin: str, files: list[Any]
) -> PlanResult:
    if req.role == "probe":
        return await _plan_probe(db, req, origin, files)
    if req.role == "features":
        return await _plan_features(db, req, origin, files)
    if req.probe is not None or req.features is not None:
        raise UnprocessableError(
            "'probe' and 'features' are for probe-verdict and feature-tag runs only.",
            code="PROBE_NOT_EXPECTED",
        )
    if req.reproduction_retry_reason is not None:
        raise UnprocessableError(
            "'reproduction_retry_reason' is for probe-verdict runs only (the reproduction gate).",
            code="PROBE_NOT_EXPECTED",
        )
    resolved = await resolve_async(db, req.role)
    template: DecisionTemplate | None = None
    rubric: Rubric | None = None
    if req.role == "classifier":
        if not req.template_id:
            raise UnprocessableError("Choose a decision template.", code="TEMPLATE_REQUIRED")
        template = await db.get(DecisionTemplate, req.template_id)
        if template is None:
            raise NotFoundError(
                f"No decision template {req.template_id}.", code="TEMPLATE_NOT_FOUND"
            )
        body = parse_template(template.body)
        try:
            check_binding(body, resolved.model_id)
        except TemplateModelMismatch as exc:
            raise ConflictError(
                str(exc),
                code=exc.code,
                details={"bound_model": exc.bound, "endpoint_model": exc.served},
            ) from None
        if body.kind != resolved.protocol:
            raise ConflictError(
                f"The classifier role speaks {resolved.protocol}; this template is for {body.kind}.",
                code="PROTOCOL_MISMATCH",
            )
        if not req.question:
            raise UnprocessableError(
                "Type the question; it is asked of every row exactly as written.",
                code="QUESTION_REQUIRED",
            )
        label_set = list(body.label_set)
        input_fields = list(body.input_fields)
        template_ref = f"{template.name}@{template.version}"
        sampling: dict[str, Any] = {}
        structured = "n/a"
        _thresholds(
            req.threshold_positive, req.threshold_negative, req.min_top_probability, label_set
        )
    else:
        if not req.rubric_id:
            raise UnprocessableError("Choose a rubric.", code="RUBRIC_REQUIRED")
        rubric = await db.get(Rubric, req.rubric_id)
        if rubric is None:
            raise NotFoundError(f"No rubric {req.rubric_id}.", code="RUBRIC_NOT_FOUND")
        rubric_body = RubricBody.model_validate(rubric.body)
        # a stored rubric predating create-time validation fails HERE, not 20 rows into the run
        check_rubric_renders(rubric_body, rubric_ref=f"{rubric.name}@{rubric.version}")
        label_set = list(rubric_body.allowed_verdicts)
        input_fields = list(rubric_body.input_fields)
        template_ref = f"{rubric.name}@{rubric.version}"
        given = req.sampling.model_dump() if req.sampling else {}
        sampling = {
            "temperature": 0.0 if given.get("temperature") is None else given["temperature"],
            "seed": given.get("seed"),
            "max_tokens": given.get("max_tokens"),
        }
        structured = (
            "json_schema"
            if rubric_body.parser == "json_v1" and rubric_body.json_schema
            else "strict_parse"
        )

    await run_in_threadpool(label_inputs.check_field_map, files, req.field_map, input_fields)
    row_filter = req.row_filter.normalised() if req.row_filter else None
    keys_table = await run_in_threadpool(label_inputs.row_keys, files, row_filter)
    keys = [str(k) for k in keys_table.column("row_key").to_pylist()]
    rows_total = len(keys)

    try:
        server = await run_in_threadpool(_probe, resolved)
    except EndpointCallError as exc:
        raise AppError(exc.message, code=exc.code, status_code=502) from None
    required_model: str | None = None
    if server.is_millm:
        required_model = resolved.model_id
        resident = server.resident.name if server.resident else None
        if resident != resolved.model_id and not await _active_model_jobs(db, resident or ""):
            raise ConflictError(
                f"{resolved.model_id} is not loaded in miLLM ({resident or 'nothing'} is). "
                f"Load {resolved.model_id} in miLLM, then start the run.",
                code="MODEL_NOT_LOADED",
                details={"needed": resolved.model_id, "resident": resident},
            )
        lease = server.lease or {}
        holder = lease.get("holder")
        if holder and holder != get_settings().millm_lease_holder:
            raise ConflictError(
                f"miLLM's model is leased by {holder} until {lease.get('expires_at')}. "
                "Start the run when that lease ends.",
                code="LEASE_HELD",
                details={"holder": holder, "expires_at": lease.get("expires_at")},
            )
    revision = server_probe.model_revision(server, resolved.model_id)
    identity = labeling_rules.labeler_identity(
        protocol=resolved.protocol,
        model_id=resolved.model_id,
        model_revision=revision,
        template_ref=template_ref,
        question=req.question,
    )
    identity_hash = labeling_rules.identity_hash(identity)
    packing = "single"
    if req.transport == "batch":
        if not (
            req.role == "classifier" and resolved.protocol == "openai_scoring" and server.is_millm
        ):
            raise UnprocessableError(
                "The batch transport serves classifier scoring on miLLM only (FR-005.35).",
                code="BATCH_UNSUPPORTED",
            )
        packing = "batch"
    fingerprint = labeling_rules.fingerprint(identity, sampling, structured, packing)
    reused = await reusable_rows(db, fingerprint, revision, keys)
    rows_to_score = rows_total - reused

    try:
        run_checks(
            PreflightContext(
                input_version_id=req.input_version_id,
                role=req.role,
                protocol=resolved.protocol,
                base_url=resolved.base_url,
                model_id=resolved.model_id,
                model_revision=revision,
                labeler_identity=identity,
                labeler_identity_hash=identity_hash,
                row_filter=row_filter,
                rows_to_score=rows_to_score,
                template_id=req.template_id,
                rubric_id=req.rubric_id,
            )
        )
    except PreflightRefused as refused:
        raise UnprocessableError(
            refused.message, code=refused.code, details=refused.details
        ) from None

    window = (
        await agent_label_ledger.window_total(db, req.input_version_id) if origin == "agent" else 0
    )
    threshold = get_settings().agent_label_row_threshold
    result_plan = Plan(
        rows_total=rows_total,
        rows_reused=reused,
        rows_to_score=rows_to_score,
        agent_window_rows=window,
        approval_needed=labeling_rules.approval_needed(
            origin=origin, rows_to_score=rows_to_score, window_rows=window, threshold=threshold
        ),
        threshold=threshold,
        labeler_identity=identity,
        labeler_identity_hash=identity_hash,
        labeler_fingerprint=fingerprint,
        server_kind=server.kind,
        resident_model=server.resident.name if server.resident else server.tei_model_id,
        model_revision=revision,
    )
    return PlanResult(
        result_plan,
        resolved,
        template,
        rubric,
        template_ref,
        label_set,
        sampling,
        structured,
        packing,
        row_filter,
        server,
        required_model,
    )


# --- the probe-verdict plan (009; operator decision 2026-10-07) ---------------------------------


@dataclass(frozen=True)
class _ProbeFacts:
    server: server_probe.ServerInfo
    probe: Any
    preflight: dict[str, Any]


def _probe_facts(base_url: str, probe_id: str, window: str, sample: Any) -> _ProbeFacts:
    """miLLM's side of the plan (a worker thread): the server, the probe, and ONE input scored
    with refuse-load and no lease, so miLLM's own refusal (identity, GGUF, scope) is shown before
    a run exists. A busy miLLM skips the preflight and says so; the worker still refuses."""
    from ..clients.endpoint_errors import RowError
    from ..clients.labelers.probe_score import ProbeScoreClient
    from .detector_sets import probe_protocol as pp

    class _Busy(Exception):
        pass

    def on_wait(seconds: float, reason: str) -> None:
        raise _Busy(reason)

    with caller_for(base_url, None, transient_retries=0, on_wait=on_wait) as caller:
        server = server_probe.detect_server(caller)
        if not server.is_millm:
            raise pp.ProbeRefused(
                "PROBE_ENDPOINT_NOT_MILLM",
                f"MILLM_BASE_URL ({base_url}) answers as a {server.kind} server, not miLLM; probe "
                "scoring needs miLLM.",
            )
        probe = pp.read_probe(caller, probe_id)
        resident = server.resident
        if resident is None:
            raise pp.ProbeRefused(
                "MODEL_NOT_LOADED",
                f"No model is loaded in miLLM. Load {probe.hf_id} (the model probe "
                f"{probe.probe_id} was fitted on), then start the run.",
                {"needed": probe.hf_id, "resident": None},
            )
        if resident.repo_id is not None and resident.repo_id != probe.hf_id:
            # Before the preflight: "load X" is the fix, and miLLM's identity report would only
            # say the probe was fitted on a different model.
            raise pp.ProbeRefused(
                "MODEL_NOT_RESIDENT",
                f"miLLM holds {resident.repo_id}, and probe {probe.probe_id} was fitted on "
                f"{probe.hf_id}. Load {probe.hf_id} in miLLM, then start the run.",
                {"needed": probe.hf_id, "resident": resident.repo_id},
            )
        preflight: dict[str, Any] = {"checked": False, "reason": "the version has no rows"}
        if sample:
            client = ProbeScoreClient(caller, [probe_id], windows=[window])
            try:
                scored = client.score(sample[0][1])
            except _Busy as busy:
                preflight = {"checked": False, "reason": f"miLLM was busy ({busy}); not checked"}
            except RowError as exc:
                refused = pp.refusal_for(exc)
                if refused is not None:
                    raise refused from None
                raise
            else:
                verdict = pp.the_verdict(scored, probe_id, window) if scored.error is None else None
                preflight = {
                    "checked": True,
                    "row_key": sample[0][0],
                    "error": scored.error,
                    "score": verdict["score"] if verdict else None,
                    "threshold": verdict["threshold"] if verdict else None,
                    "verdict": verdict["verdict"] if verdict else None,
                    "provisional": verdict["provisional"] if verdict else None,
                    "model": pp.reported_model(scored),
                    # Finding 4: which bar the number is (a length band or the window's bar).
                    "bar": (
                        pp.bar_source(
                            probe,
                            window,
                            verdict["threshold"],
                            verdict.get("n_scored_tokens", scored.n_tokens),
                        )
                        if verdict
                        else None
                    ),
                }
        return _ProbeFacts(server, probe, preflight)


def _refuse_labeler_fields(req: LabelRunStart, what: str, why: str) -> None:
    """Probe-verdict and feature-tag runs take none of a classifier's or judge's fields."""
    extra = {
        "template_id": req.template_id,
        "rubric_id": req.rubric_id,
        "question": req.question,
        "threshold_positive": req.threshold_positive,
        "threshold_negative": req.threshold_negative,
        "min_top_probability": req.min_top_probability,
        "sampling": req.sampling,
        "keep_share_job_id": req.keep_share_job_id,
    }
    given = sorted(k for k, v in extra.items() if v is not None)
    if given or req.transport != "single":
        raise UnprocessableError(
            f"{what} takes no template, rubric, question, thresholds, sampling or batch "
            f"transport: {why} (got {given or ['transport']}).",
            code="PROBE_FIELDS_INVALID",
            details={"fields": given},
        )


def _link_candidates(mistudio_probe_id: str | None) -> dict[str, Any]:
    """The evaluations miStudio recorded for the source probe, for a reproduction link; best
    effort, never a reason the plan fails differently. miStudio not configured is said by name."""
    from ..clients import mistudio_client as mc
    from .detector_sets import reproduction_links

    if not mistudio_probe_id or mistudio_probe_id == "not reported":
        return {"items": [], "reason": "miLLM records no source miStudio probe for this probe."}
    base = get_settings().mistudio_base_url
    if not base:
        return {
            "items": [],
            "reason": "miStudio is not configured (MISTUDIO_BASE_URL), so its recorded "
            "evaluations cannot be listed or linked.",
        }
    try:
        with mc.MiStudioClient(base) as client:
            items = reproduction_links.candidates(client, mistudio_probe_id)
    except mc.MiStudioError as exc:
        return {"items": [], "reason": f"miStudio's evaluations could not be read: {exc.message}"}
    return {
        "items": items,
        "reason": None if items else f"miStudio records no evaluation of {mistudio_probe_id}.",
    }


async def _plan_probe(
    db: AsyncSession, req: LabelRunStart, origin: str, files: list[Any]
) -> PlanResult:
    from .detector_sets import probe_protocol as pp
    from .detector_sets import reproduction

    if req.probe is None or req.features is not None:
        raise UnprocessableError(
            "Choose the miLLM probe to score with (and no feature-tag read).",
            code="PROBE_REQUIRED",
        )
    _refuse_labeler_fields(req, "A probe-verdict run", "the probe's own bar decides")
    try:
        form = pp.input_form(req.field_map)
        base_url = pp.millm_base_url()
    except pp.ProbeRefused as refused:
        raise refused.as_app_error(422 if refused.code == "FIELD_MAP_INVALID" else 409) from None
    field = next(iter(req.field_map))
    await run_in_threadpool(label_inputs.check_field_map, files, req.field_map, [field])
    row_filter = req.row_filter.normalised() if req.row_filter else None
    column = req.field_map[field]
    kind = await run_in_threadpool(label_inputs.column_type, files, column)
    looked = await run_in_threadpool(
        label_inputs.sample_rows, files, req.field_map, row_filter, pp.JSON_CHAT_SAMPLE, 0
    )
    try:
        pp.check_input_column(column, kind, form, [fields[field] for _, fields in looked])
    except pp.ProbeRefused as refused:
        raise refused.as_app_error(422) from None
    keys_table = await run_in_threadpool(label_inputs.row_keys, files, row_filter)
    keys = [str(k) for k in keys_table.column("row_key").to_pylist()]
    rows_total = len(keys)
    sample = await run_in_threadpool(
        label_inputs.sample_rows, files, req.field_map, row_filter, 1, 0
    )
    try:
        facts = await run_in_threadpool(
            _probe_facts, base_url, req.probe.probe_id, req.probe.window, sample
        )
    except pp.ProbeRefused as refused:
        raise refused.as_app_error() from None
    except EndpointCallError as exc:
        raise AppError(exc.message, code=exc.code, status_code=502) from None
    server, probe = facts.server, facts.probe
    resident = server.resident
    assert resident is not None  # _probe_facts refuses a miLLM with nothing loaded
    lease = server.lease or {}
    holder = lease.get("holder")
    if holder and holder != get_settings().millm_lease_holder:
        raise ConflictError(
            f"miLLM's model is leased by {holder} until {lease.get('expires_at')}. "
            "Start the run when that lease ends.",
            code="LEASE_HELD",
            details={"holder": holder, "expires_at": lease.get("expires_at")},
        )
    revision = server_probe.model_revision(server, resident.name)
    identity = pp.identity(
        probe,
        model_id=resident.name,
        model_revision=revision,
        window=req.probe.window,
        form=form,
    )
    identity_hash = labeling_rules.identity_hash(identity)
    fingerprint = labeling_rules.fingerprint(identity, {}, "n/a", "single")
    reused = await reusable_rows(db, fingerprint, revision, keys)
    rows_to_score = rows_total - reused

    retry_reason = req.reproduction_retry_reason

    def _reproduction() -> dict[str, Any]:
        from ..core.database import get_sync_db

        with get_sync_db() as session:
            cached = reproduction.passed_before(session, identity)
            if cached is not None:
                return cached
            target = reproduction.target_for(session, probe.mistudio_probe_id)
            counted = {"row_keys": reproduction.target_keys(session, target)}
            # 2026-10-08 finding 3: only a pass is cached, so the same check that failed would fail
            # the same way. The plan says so, with the figures and the run that failed, unless the
            # operator deliberately retries with a reason.
            failed = reproduction.failed_before(session, identity, target)
            if failed is not None and not retry_reason:
                return {**failed, **counted}
            out = {**target.as_dict(), **counted, "state": "will_run"}
            if failed is not None:
                out["retry_of"] = {
                    "run_id": failed["failed_run_id"],
                    "millm_auroc": failed.get("millm_auroc"),
                    "reason": retry_reason,
                }
            return out

    try:
        gate = await run_in_threadpool(_reproduction)
    except reproduction.ReproductionUnavailable as missing:
        # The refusal stays a refusal, and its body carries what the plan already learned (defect
        # found by the 2026-10-07 live check: the preflight was run and then thrown away): the
        # probe, the one-input preflight against the bar, the reproduction state and both ways
        # through, with the evaluations miStudio recorded that a link could name.
        found = await run_in_threadpool(_link_candidates, probe.mistudio_probe_id)
        raise UnprocessableError(
            missing.message,
            code="REPRODUCTION_UNAVAILABLE",
            details={
                "mistudio_probe_id": probe.mistudio_probe_id,
                "probe": {**probe.as_dict(req.probe.window), "preflight": facts.preflight},
                "reproduction": {
                    "state": "unavailable",
                    "reason": missing.message,
                    "ways": list(reproduction.WAYS_THROUGH),
                },
                "link_candidates": found,
            },
        ) from None
    resolved = ResolvedRoleEndpoint(pp.ROLE, pp.PROTOCOL, base_url, resident.name, None)
    try:
        run_checks(
            PreflightContext(
                input_version_id=req.input_version_id,
                role=pp.ROLE,
                protocol=pp.PROTOCOL,
                base_url=base_url,
                model_id=resident.name,
                model_revision=revision,
                labeler_identity=identity,
                labeler_identity_hash=identity_hash,
                row_filter=row_filter,
                rows_to_score=rows_to_score,
                template_id=None,
                rubric_id=None,
            )
        )
    except PreflightRefused as refused:
        raise UnprocessableError(
            refused.message, code=refused.code, details=refused.details
        ) from None
    window_rows = (
        await agent_label_ledger.window_total(db, req.input_version_id) if origin == "agent" else 0
    )
    threshold = get_settings().agent_label_row_threshold
    result_plan = Plan(
        rows_total=rows_total,
        rows_reused=reused,
        rows_to_score=rows_to_score,
        agent_window_rows=window_rows,
        approval_needed=labeling_rules.approval_needed(
            origin=origin, rows_to_score=rows_to_score, window_rows=window_rows, threshold=threshold
        ),
        threshold=threshold,
        labeler_identity=identity,
        labeler_identity_hash=identity_hash,
        labeler_fingerprint=fingerprint,
        server_kind=server.kind,
        resident_model=resident.name,
        model_revision=revision,
        probe={**probe.as_dict(req.probe.window), "preflight": facts.preflight},
        reproduction=gate,
    )
    return PlanResult(
        result_plan,
        resolved,
        None,
        None,
        f"probe:{probe.probe_id}",
        ["positive", "negative"],
        {},
        "n/a",
        "single",
        row_filter,
        server,
        resident.name,
    )


# --- the feature-tag plan (009 FR-009.65 - FR-009.68; same decision as probe verdicts) ---------


def _feature_facts(base_url: str, spec: Any, sample: Any) -> dict[str, Any]:
    """miLLM's side of a feature-tag plan: the resident model, the SAE it will read (one attached,
    or the one named) and a one-row preflight read, so miLLM's refusal shows before a run."""
    from ..clients.endpoint_errors import RowError
    from ..clients.labelers.sae_activations import SaeActivationsClient
    from .detector_sets import feature_protocol as fp
    from .detector_sets import probe_protocol as pp

    class _Busy(Exception):
        pass

    def on_wait(seconds: float, reason: str) -> None:
        raise _Busy(reason)

    with caller_for(base_url, None, transient_retries=0, on_wait=on_wait) as caller:
        server = server_probe.detect_server(caller)
        if not server.is_millm:
            raise pp.ProbeRefused(
                "PROBE_ENDPOINT_NOT_MILLM",
                f"MILLM_BASE_URL ({base_url}) answers as a {server.kind} server, not miLLM.",
            )
        if server.resident is None:
            raise pp.ProbeRefused(
                "MODEL_NOT_LOADED", "No model is loaded in miLLM. Load one, then start the run."
            )
        attached = caller.raw("GET", "/api/saes/attachments")
        entries = (
            list(attached.body["data"]["entries"])
            if attached.status == 200 and isinstance(attached.body, dict)
            else []
        )
        if spec.sae_id is not None:
            chosen = [e for e in entries if e["sae_id"] == spec.sae_id]
        else:
            chosen = entries
        if len(chosen) != 1:
            named = ", ".join(f"{e['sae_id']} (layer {e['layer']})" for e in entries) or "none"
            raise pp.ProbeRefused(
                "SAE_NOT_ATTACHED",
                (
                    f"SAE {spec.sae_id} is not attached in miLLM"
                    if spec.sae_id is not None
                    else "Name the SAE to read: miLLM has " + str(len(entries)) + " attached"
                )
                + f" (attached: {named}). Attach it in miLLM, then start the run.",
                {"attached": entries},
            )
        sae = chosen[0]
        preflight: dict[str, Any] = {"checked": False, "reason": "the version has no rows"}
        if sample:
            client = SaeActivationsClient(
                caller,
                server.resident.name,
                sae_id=str(sae["sae_id"]),
                top_k=spec.top_k,
                positions=spec.positions,
                features=spec.features,
            )
            try:
                read = client.read(sample[0][1])
            except _Busy as busy:
                preflight = {"checked": False, "reason": f"miLLM was busy ({busy}); not checked"}
            except RowError as exc:
                refused = fp.refusal_for(exc)
                if refused is not None:
                    raise refused from None
                raise
            else:
                block = read.block or {}
                preflight = {
                    "checked": True,
                    "row_key": sample[0][0],
                    "read_point": block.get("read_point", "not reported"),
                    "positions": len(block.get("positions") or []),
                }
                if block.get("read_point") != fp.READ_POINT:
                    raise pp.ProbeRefused(
                        "READ_POINT_MISMATCH",
                        f"miLLM read the SAE at {block.get('read_point', 'no read point')!r}, not "
                        f"{fp.READ_POINT!r}; tagging must read unsteered (X-09).",
                    )
        return {
            "server": server,
            "sae_id": str(sae["sae_id"]),
            "layer": sae.get("layer"),
            "preflight": preflight,
        }


async def _plan_features(
    db: AsyncSession, req: LabelRunStart, origin: str, files: list[Any]
) -> PlanResult:
    from .detector_sets import feature_protocol as fp
    from .detector_sets import probe_protocol as pp

    spec = req.features
    if spec is None or req.probe is not None:
        raise UnprocessableError(
            "Name the SAE read (top_k, positions) for a feature-tag run.",
            code="FEATURES_REQUIRED",
        )
    _refuse_labeler_fields(req, "A feature-tag run", "it records SAE activations, not a verdict")
    if sorted(req.field_map) != ["text"]:
        raise UnprocessableError(
            'A feature-tag run reads one text column: map "text" to it.',
            code="FIELD_MAP_INVALID",
            details={"field_map": dict(req.field_map)},
        )
    try:
        base_url = pp.millm_base_url()
    except pp.ProbeRefused as refused:
        raise refused.as_app_error() from None
    await run_in_threadpool(label_inputs.check_field_map, files, req.field_map, ["text"])
    row_filter = req.row_filter.normalised() if req.row_filter else None
    keys_table = await run_in_threadpool(label_inputs.row_keys, files, row_filter)
    keys = [str(k) for k in keys_table.column("row_key").to_pylist()]
    sample = await run_in_threadpool(
        label_inputs.sample_rows, files, req.field_map, row_filter, 1, 0
    )
    try:
        facts = await run_in_threadpool(_feature_facts, base_url, spec, sample)
    except pp.ProbeRefused as refused:
        raise refused.as_app_error() from None
    except EndpointCallError as exc:
        raise AppError(exc.message, code=exc.code, status_code=502) from None
    server = facts["server"]
    resident = server.resident
    lease = server.lease or {}
    holder = lease.get("holder")
    if holder and holder != get_settings().millm_lease_holder:
        raise ConflictError(
            f"miLLM's model is leased by {holder} until {lease.get('expires_at')}. "
            "Start the run when that lease ends.",
            code="LEASE_HELD",
            details={"holder": holder, "expires_at": lease.get("expires_at")},
        )
    revision = server_probe.model_revision(server, resident.name)
    identity = fp.identity(
        model_id=resident.name,
        model_revision=revision,
        sae_id=facts["sae_id"],
        layer=facts["layer"],
        top_k=spec.top_k,
        positions=spec.positions,
        features=spec.features,
    )
    identity_hash = labeling_rules.identity_hash(identity)
    fingerprint = labeling_rules.fingerprint(identity, {}, "n/a", "single")
    reused = await reusable_rows(db, fingerprint, revision, keys)
    rows_to_score = len(keys) - reused
    try:
        run_checks(
            PreflightContext(
                input_version_id=req.input_version_id,
                role=fp.ROLE,
                protocol=fp.PROTOCOL,
                base_url=base_url,
                model_id=resident.name,
                model_revision=revision,
                labeler_identity=identity,
                labeler_identity_hash=identity_hash,
                row_filter=row_filter,
                rows_to_score=rows_to_score,
                template_id=None,
                rubric_id=None,
            )
        )
    except PreflightRefused as refused:
        raise UnprocessableError(
            refused.message, code=refused.code, details=refused.details
        ) from None
    window_rows = (
        await agent_label_ledger.window_total(db, req.input_version_id) if origin == "agent" else 0
    )
    threshold = get_settings().agent_label_row_threshold
    result_plan = Plan(
        rows_total=len(keys),
        rows_reused=reused,
        rows_to_score=rows_to_score,
        agent_window_rows=window_rows,
        approval_needed=labeling_rules.approval_needed(
            origin=origin, rows_to_score=rows_to_score, window_rows=window_rows, threshold=threshold
        ),
        threshold=threshold,
        labeler_identity=identity,
        labeler_identity_hash=identity_hash,
        labeler_fingerprint=fingerprint,
        server_kind=server.kind,
        resident_model=resident.name,
        model_revision=revision,
        features={
            "sae_id": facts["sae_id"],
            "layer": facts["layer"],
            "top_k": spec.top_k,
            "positions": spec.positions,
            "features": spec.features,
            "preflight": facts["preflight"],
        },
    )
    resolved = ResolvedRoleEndpoint(fp.ROLE, fp.PROTOCOL, base_url, resident.name, None)
    return PlanResult(
        result_plan,
        resolved,
        None,
        None,
        f"sae:{facts['sae_id']}",
        [fp.OUTCOME],
        {},
        "n/a",
        "single",
        row_filter,
        server,
        resident.name,
    )


# --- start ------------------------------------------------------------------------------------


def _dispatch() -> list[str]:
    from ..core.database import get_sync_db
    from .job_service import dispatch_queued

    with get_sync_db() as session:
        return dispatch_queued(session)


async def approved_rows(db: AsyncSession, approval_id: str) -> int | None:
    """``rows_to_score`` the operator approved, from the stored request (FTDD 005 section 5.4)."""
    approval = await db.get(Approval, approval_id)
    if approval is None:
        return None
    stored = approval.payload.get("plan") if isinstance(approval.payload, dict) else None
    value = stored.get("rows_to_score") if isinstance(stored, dict) else None
    return int(value) if isinstance(value, int) else None


async def start(
    db: AsyncSession, req: LabelRunStart, result: PlanResult, who: Who, actor: Actor
) -> LabelRun:
    settings = get_settings()
    plan_ = result.plan
    snapshot: dict[str, Any] = {
        **result.resolved.snapshot(),
        "server_kind": result.server.kind,
        "model_revision": plan_.model_revision,
        "fingerprint": plan_.labeler_fingerprint,
    }
    if req.role == "probe":
        assert req.probe is not None and plan_.probe is not None
        gate = plan_.reproduction or {}
        if gate.get("state") == "failed":
            raise ConflictError(
                f"The reproduction check already failed for this probe, model, revision, window, "
                f"input form and target on run {gate.get('failed_run_id')} (miLLM AUROC "
                f"{gate.get('millm_auroc')}, miStudio's interval {gate.get('mistudio_ci')}). "
                + str(gate.get("retry")),
                code="REPRODUCTION_FAILED_BEFORE",
                details={"reproduction": gate},
            )
        if gate.get("retry_of"):
            snapshot["reproduction_retry"] = {
                **gate["retry_of"],
                "by": who.who,
                "origin": who.origin,
                "at": utc_now().isoformat(),
            }
        snapshot["probe"] = {
            **plan_.probe,
            "window": req.probe.window,
        }
        snapshot["probe"].pop("preflight", None)
        snapshot["input_form"] = plan_.labeler_identity["input_form"]
    if req.role == "features":
        assert plan_.features is not None
        snapshot["features"] = {k: v for k, v in plan_.features.items() if k != "preflight"}
    run = LabelRun(
        id=new_id("lr"),
        kind={"probe": "probe_verdict", "features": "feature_tag"}.get(req.role, req.role),
        state="queued",
        input_version_id=req.input_version_id,
        field_map=dict(req.field_map),
        endpoint_snapshot=snapshot,
        template_id=result.template.id if result.template else None,
        rubric_id=result.rubric.id if result.rubric else None,
        question=req.question,
        positive_label=req.positive_label,
        negative_label=req.negative_label,
        threshold_positive=req.threshold_positive,
        threshold_negative=req.threshold_negative,
        min_top_probability=req.min_top_probability,
        label_set=result.label_set,
        sampling=result.sampling,
        structured_output=result.structured_output,
        packing=result.packing,
        chunk_size=req.chunk_size or settings.label_chunk_size,
        row_filter=result.row_filter,
        parent_run_ids=[],
        labeler_identity=plan_.labeler_identity,
        labeler_identity_hash=plan_.labeler_identity_hash,
        labeler_fingerprint=plan_.labeler_fingerprint,
        revision_reported=plan_.model_revision is not None,
        counts={},
        rows_total=plan_.rows_total,
        row_coverage=plan_.row_coverage,
        rows_reused=0,
        agent_counted_rows=plan_.rows_to_score if who.origin == "agent" else 0,
        approval_id=actor.approval_id,
        started_by=who.who,
        started_by_origin=who.origin,
    )
    if req.keep_share_job_id:
        run.keep_share_estimate = await _estimate(db, req.keep_share_job_id)
    db.add(run)
    job = _new_job(run, "label_run", who, result.required_model_id)
    db.add(job)
    await db.flush()
    db.add(LabelRunJob(label_run_id=run.id, seq=0, job_id=job.id))
    if who.origin == "agent":
        # FR-009.74: a probe-verdict run counts under its own run kind in the shared ledger.
        ledger_kind = {"probe_verdict": "probe_verdict", "feature_tag": "feature_tagging"}.get(
            run.kind, "label_run"
        )
        await agent_label_ledger.admit(
            db, req.input_version_id, who.who, ledger_kind, run.id, plan_.rows_to_score
        )
    await db.commit()
    logger.info(
        "label_run.started run=%s job=%s rows_to_score=%d origin=%s",
        run.id,
        job.id,
        plan_.rows_to_score,
        who.origin,
    )
    await run_in_threadpool(_dispatch)
    return run


async def _estimate(db: AsyncSession, job_id: str) -> dict[str, Any] | None:
    """A completed keep-share preview's ``{share, lo, hi, n}``; anything else records nothing."""
    job = await db.get(Job, job_id)
    if job is None or job.kind != "label_preview" or job.status != "completed" or not job.result:
        return None
    return {k: job.result.get(k) for k in ("share", "lo", "hi", "n", "seed")}


def _new_job(run: LabelRun, kind: str, who: Who, required_model: str | None) -> Job:
    get_job_kind(kind)
    return Job(
        id=new_id("job"),
        kind=kind,
        status="queued",
        progress=0.0,
        params={"label_run_id": run.id},
        started_by=who.who,
        started_by_origin=who.origin,
        required_model_id=required_model,
    )


# --- lifecycle --------------------------------------------------------------------------------


async def get_run(db: AsyncSession, run_id: str) -> LabelRun:
    run = await db.get(LabelRun, run_id, populate_existing=True)
    if run is None:
        raise NotFoundError(f"No label run {run_id}.", code="LABEL_RUN_NOT_FOUND")
    return run


async def _jobs(db: AsyncSession, run_id: str) -> list[Job]:
    rows = await db.execute(
        select(Job)
        .join(LabelRunJob, LabelRunJob.job_id == Job.id)
        .where(LabelRunJob.label_run_id == run_id)
        .order_by(LabelRunJob.seq)
        .execution_options(populate_existing=True)
    )
    return list(rows.scalars())


async def cancel(db: AsyncSession, run_id: str) -> LabelRun:
    from .job_service import JobService

    run = await get_run(db, run_id)
    jobs = await _jobs(db, run_id)
    live = [j for j in jobs if j.status not in TERMINAL_STATUSES]
    if not live:
        raise ConflictError(
            f"Label run {run_id} is {run.state}; there is nothing to cancel.",
            code="RUN_NOT_CANCELLABLE",
            details={"state": run.state},
        )
    job, _ = await JobService.cancel(db, live[-1].id, "Cancelled by the operator.")
    if job.status == "cancelled":  # never started: the worker will not mark it
        run = await get_run(db, run_id)
        run.state = "cancelled"
        await db.commit()
    return await get_run(db, run_id)


#: A run stopped by the reproduction gate: resuming scores the same rows the same way.
GATE_FAILURE_CODES = frozenset({"REPRODUCTION_FAILED", "REPRODUCTION_FAILED_BEFORE"})


def not_resumable_reason(run: LabelRun) -> str | None:
    """Why a cancelled or failed run cannot be resumed, or None when it can (2026-10-08 finding 3:
    a gate-failed run offered "Resume run", which could only fail the same way)."""
    code = (run.error or {}).get("code")
    if run.state == "failed" and code in GATE_FAILURE_CODES:
        from .detector_sets.reproduction import RETRY_GUIDANCE

        return (
            f"Label run {run.id} stopped at the reproduction check, and a resume would run the same "
            f"check against the same rows. {RETRY_GUIDANCE} That is a new run."
        )
    return None


async def resume(db: AsyncSession, run_id: str, who: Who) -> LabelRun:
    """A new job continues the same run (FR-005.33; ADR-007). No approval: a resume re-sends no
    counted row (P-07)."""
    run = await get_run(db, run_id)
    jobs = await _jobs(db, run_id)
    if run.state not in RESUMABLE_RUN_STATES or any(
        j.status not in TERMINAL_STATUSES for j in jobs
    ):
        raise ConflictError(
            f"Label run {run_id} is {run.state}; only a cancelled or failed run can be resumed.",
            code="RUN_NOT_RESUMABLE",
            details={"state": run.state},
        )
    blocked = not_resumable_reason(run)
    if blocked is not None:
        raise ConflictError(blocked, code="RUN_GATE_FAILED", details={"error": run.error})
    kind = {"rederived": "label_rederive", "aggregate": "label_aggregate"}.get(
        run.kind, "label_run"
    )
    required = (
        run.endpoint_snapshot.get("model_id")
        if run.endpoint_snapshot.get("server_kind") == "millm" and kind == "label_run"
        else None
    )
    job = _new_job(run, kind, who, required)
    db.add(job)
    await db.flush()
    db.add(LabelRunJob(label_run_id=run.id, seq=len(jobs), job_id=job.id))
    run.state = "queued"
    run.error = None
    run.completed_at = None
    await db.commit()
    await run_in_threadpool(_dispatch)
    return await get_run(db, run_id)


async def rederive(
    db: AsyncSession,
    parent_id: str,
    threshold_positive: float | None,
    threshold_negative: float | None,
    min_top_probability: float | None,
    who: Who,
) -> LabelRun:
    """A child run with new thresholds from the stored probabilities; no endpoint call
    (FR-005.21). The parent never changes."""
    parent = await get_run(db, parent_id)
    if parent.kind not in ("classifier", "rederived") or parent.state != "completed":
        raise ConflictError(
            "Only a completed classifier run can be re-derived.",
            code="RUN_NOT_REDERIVABLE",
            details={"kind": parent.kind, "state": parent.state},
        )
    _thresholds(
        threshold_positive, threshold_negative, min_top_probability, list(parent.label_set or [])
    )
    child = LabelRun(
        id=new_id("lr"),
        kind="rederived",
        state="queued",
        input_version_id=parent.input_version_id,
        field_map=dict(parent.field_map),
        endpoint_snapshot=dict(parent.endpoint_snapshot),
        template_id=parent.template_id,
        rubric_id=None,
        question=parent.question,
        positive_label=parent.positive_label,
        negative_label=parent.negative_label,
        threshold_positive=threshold_positive,
        threshold_negative=threshold_negative,
        min_top_probability=min_top_probability,
        label_set=parent.label_set,
        sampling=dict(parent.sampling),
        structured_output=parent.structured_output,
        packing=parent.packing,
        chunk_size=parent.chunk_size,
        row_filter=parent.row_filter,
        parent_run_ids=[parent.id],
        labeler_identity=dict(parent.labeler_identity),
        labeler_identity_hash=parent.labeler_identity_hash,
        labeler_fingerprint=parent.labeler_fingerprint,
        pinned=parent.pinned,
        revision_reported=parent.revision_reported,
        system_fingerprint=parent.system_fingerprint,
        counts={},
        rows_total=parent.rows_total,
        rows_reused=0,
        agent_counted_rows=0,
        started_by=who.who,
        started_by_origin=who.origin,
    )
    db.add(child)
    job = _new_job(child, "label_rederive", who, None)
    db.add(job)
    await db.flush()
    db.add(LabelRunJob(label_run_id=child.id, seq=0, job_id=job.id))
    await db.commit()
    await run_in_threadpool(_dispatch)
    return child


async def aggregate(db: AsyncSession, run_ids: list[str], who: Who) -> LabelRun:
    """Combine two or more completed judge runs on one input version (FR-005.45)."""
    if len(set(run_ids)) != len(run_ids):
        raise UnprocessableError("Name each judge run once.", code="AGGREGATE_DUPLICATE")
    parents = [await get_run(db, rid) for rid in run_ids]
    if any(p.kind != "judge" or p.state != "completed" for p in parents):
        raise ConflictError(
            "An aggregate combines completed judge runs only.", code="RUN_NOT_AGGREGATABLE"
        )
    versions = {p.input_version_id for p in parents}
    if len(versions) != 1:
        raise ConflictError(
            "The judge runs label different versions; aggregate runs over the same rows.",
            code="RUN_NOT_AGGREGATABLE",
        )
    identity = {"aggregate_of": sorted(p.labeler_identity_hash for p in parents)}
    first = parents[0]
    child = LabelRun(
        id=new_id("lr"),
        kind="aggregate",
        state="queued",
        input_version_id=first.input_version_id,
        field_map=dict(first.field_map),
        endpoint_snapshot={"aggregate_of": [p.id for p in parents]},
        rubric_id=None,
        question=first.question,
        label_set=first.label_set,
        sampling={},
        structured_output="n/a",
        packing="single",
        chunk_size=first.chunk_size,
        parent_run_ids=[p.id for p in parents],
        labeler_identity=identity,
        labeler_identity_hash=labeling_rules.identity_hash(identity),
        labeler_fingerprint=labeling_rules.fingerprint(identity, {}, "n/a", "single"),
        pinned=all(bool(p.pinned) for p in parents),
        revision_reported=all(bool(p.revision_reported) for p in parents),
        counts={},
        rows_total=max(p.rows_total for p in parents),
        rows_reused=0,
        agent_counted_rows=0,
        started_by=who.who,
        started_by_origin=who.origin,
    )
    db.add(child)
    job = _new_job(child, "label_aggregate", who, None)
    db.add(job)
    await db.flush()
    db.add(LabelRunJob(label_run_id=child.id, seq=0, job_id=job.id))
    await db.commit()
    await run_in_threadpool(_dispatch)
    return child


# --- reads ------------------------------------------------------------------------------------


async def run_out(db: AsyncSession, run: LabelRun) -> LabelRunOut:
    jobs = await _jobs(db, run.id)
    done = int(
        (
            await db.execute(
                select(func.count()).select_from(Label).where(Label.label_run_id == run.id)
            )
        ).scalar_one()
    )
    ref = None
    if run.template_id:
        template = await db.get(DecisionTemplate, run.template_id)
        ref = f"{template.name}@{template.version}" if template else None
    elif run.rubric_id:
        rubric = await db.get(Rubric, run.rubric_id)
        ref = f"{rubric.name}@{rubric.version}" if rubric else None
    current = next((j.id for j in reversed(jobs) if j.status not in TERMINAL_STATUSES), None)
    return LabelRunOut(
        id=run.id,
        kind=run.kind,
        state=run.state,
        input_version_id=str(run.input_version_id),
        field_map=run.field_map,
        endpoint_snapshot=run.endpoint_snapshot,
        template_id=run.template_id,
        rubric_id=run.rubric_id,
        template_ref=ref,
        question=run.question,
        positive_label=run.positive_label,
        negative_label=run.negative_label,
        threshold_positive=run.threshold_positive,
        threshold_negative=run.threshold_negative,
        min_top_probability=run.min_top_probability,
        label_set=run.label_set,
        sampling=run.sampling,
        structured_output=run.structured_output,
        packing=run.packing,
        batch_id=run.batch_id,
        chunk_size=run.chunk_size,
        row_filter=run.row_filter,
        parent_run_ids=list(run.parent_run_ids),
        labeler_identity=run.labeler_identity,
        labeler_identity_hash=run.labeler_identity_hash,
        labeler_fingerprint=run.labeler_fingerprint,
        pinned=run.pinned,
        revision_reported=run.revision_reported,
        system_fingerprint=run.system_fingerprint,
        counts=run.counts,
        keep_share_estimate=run.keep_share_estimate,
        keep_share_actual=run.keep_share_actual,
        length_correlation=run.length_correlation,
        rows_total=run.rows_total,
        rows_reused=run.rows_reused,
        rows_done=done,
        agent_counted_rows=run.agent_counted_rows,
        approval_id=run.approval_id,
        error=run.error,
        started_by=run.started_by,
        started_by_origin=run.started_by_origin,
        created_at=run.created_at,
        completed_at=run.completed_at,
        job_ids=[j.id for j in jobs],
        current_job_id=current,
        room=get_job_kind("label_run").room(run.id),
        resumable=run.state in RESUMABLE_RUN_STATES
        and not any(j.status not in TERMINAL_STATUSES for j in jobs)
        and not_resumable_reason(run) is None,
        not_resumable_reason=not_resumable_reason(run),
        row_coverage=run.row_coverage,
    )


async def list_runs(
    db: AsyncSession,
    *,
    input_version_id: str | None,
    state: str | None,
    kind: str | None,
    page: int,
    limit: int,
) -> tuple[list[LabelRun], int]:
    query = select(LabelRun)
    if input_version_id:
        query = query.where(LabelRun.input_version_id == input_version_id)
    if state:
        query = query.where(LabelRun.state == state)
    if kind:
        query = query.where(LabelRun.kind == kind)
    total = int((await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one())
    rows = await db.execute(
        query.order_by(LabelRun.created_at.desc(), LabelRun.id)
        .offset((page - 1) * limit)
        .limit(limit)
    )
    return list(rows.scalars()), total


async def labels_page(
    db: AsyncSession, run_id: str, *, outcome: str | None, page: int, limit: int
) -> LabelPage:
    run = await get_run(db, run_id)
    query = select(Label).where(Label.label_run_id == run_id)
    if outcome:
        query = query.where(Label.outcome == outcome)
    total = int((await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one())
    rows = await db.execute(query.order_by(Label.row_key).offset((page - 1) * limit).limit(limit))
    items = [
        LabelOut(
            label_run_id=row.label_run_id,
            row_key=row.row_key,
            labeler_fingerprint=row.labeler_fingerprint,
            outcome=row.outcome,
            parsed_value=row.parsed_value,
            probability=row.probability,
            distribution=row.distribution,
            raw_output=row.raw_output,
            rationale=row.rationale,
            steering_state=row.steering_state,
            latency_ms=row.latency_ms,
            skip_reason=row.skip_reason,
            provisional=row.provisional,
            reused_from_run_id=row.reused_from_run_id,
            chunk_index=row.chunk_index,
            scored_at=row.scored_at,
            started_by=run.started_by,
            started_by_origin=run.started_by_origin,
        )
        for row in rows.scalars()
    ]
    return LabelPage(items=items, total=total, page=page, limit=limit)


# --- worker-side state writes -----------------------------------------------------------------


def set_state(
    session: Session,
    run: LabelRun,
    state: str,
    *,
    error: dict[str, Any] | None = None,
) -> None:
    """The ONE writer of ``LabelRun.state`` on the worker side."""
    run.state = state
    if error is not None:
        run.error = error
    if state in TERMINAL_RUN_STATES:
        run.completed_at = utc_now()
    session.commit()


async def binding_record(db: AsyncSession, run_id: str) -> dict[str, Any] | None:
    """Feature 002's binding resolver for ``label_run`` (FR-002.2, C-002.8)."""
    run = await db.get(LabelRun, run_id)
    if run is None:
        return None
    rows = int(
        (
            await db.execute(
                select(func.count()).select_from(Label).where(Label.label_run_id == run_id)
            )
        ).scalar_one()
    )
    return {
        "complete": run.state == "completed",
        "fingerprint": run.labeler_fingerprint,
        "rows": rows,
    }
