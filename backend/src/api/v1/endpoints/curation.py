"""Feature 004's REST routes (FTDD 004 §5.1; FPRD §7.1).

"Run" routes answer ``200`` with a stored report when an identical one exists, ``200`` after
computing inline when the input is under ``CURATION_INLINE_MAX_ROWS``, or ``202 {job_id}`` with a
``curation_report`` job. Read routes answer ``404 <kind>_not_run`` when nothing is stored.

Level writes by an agent wait for the operator (``warning_level_write``; P-09 as amended
2026-11-07, ENH-006 G30) and need a reason. Approved, the change is recorded as the operator's,
with the agent and approval named in the reason, so the table's ``origin = 'operator'`` check
stays the second wall. No route here
accepts a filesystem path: every path is built from a version id by feature 002's storage helpers.
Operator previews, statistics and recipe builds use features 003's and 002's routes.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ....core.agent_origin import Actor, get_actor, requires_approval_when_agent, resolve_who
from ....core.config import get_settings
from ....core.database import get_db, sync_session_factory
from ....core.errors import AppError
from ....core.ids import new_id
from ....core.job_kinds import get_job_kind
from ....core.settings_pin import SETTINGS_PIN
from ....models.job import Job
from ....rules.next_step import next_for
from ....schemas.curation import (
    AuditRun,
    ContaminationRun,
    InputRef,
    LeakageRun,
    LevelClear,
    LevelSet,
    ProfileRun,
    TrlRun,
    VersionCheckRun,
)
from ....services.curation import api as curation_api
from ....services.curation import level_service, report_service
from ....services.curation.codes import ReportInput
from ....services.curation.errors import CurationError
from ....services.curation.kinds import compute_for, guarded
from ....services.duck import TEXT_CONTENT
from ....services.job_service import dispatch_queued
from ._paging import OptionalPage, optional_paging

router = APIRouter(prefix="/api/v1", tags=["curation"])


PAGE_MAX = 100


def _sync(fn: Callable[[Session], Any]) -> Any:
    def run() -> Any:
        with sync_session_factory()() as session:
            return fn(session)

    return run_in_threadpool(run)


def report_out(report: Any) -> dict[str, Any]:
    return {
        "id": report.id,
        "kind": report.kind,
        "state": report.state,
        "operator": f"{report.operator_name}@{report.operator_version}",
        "params": report.params,
        "inputs": report.inputs,
        "seed": report.seed,
        "result": report.result,
        "artefacts": report.artefacts,
        "job_id": report.job_id,
        "started_by": report.started_by,
        "completed_at": report.completed_at.isoformat() if report.completed_at else None,
    }


def _inputs(version_id: str, extra: list[InputRef] | None) -> list[ReportInput]:
    items = [ReportInput(version_id)]
    for ref in extra or []:
        candidate = ReportInput(ref.version_id, ref.split, ref.role)
        if candidate not in items:
            items.append(candidate)
    if extra:
        # an explicit input list replaces the bare path version
        items = [ReportInput(r.version_id, r.split, r.role) for r in extra]
        if items[0].version_id != version_id:
            items.insert(0, ReportInput(version_id))
    return items


def _rows(session: Session, inputs: list[ReportInput]) -> int:
    total = 0
    for ri in inputs:
        version = curation_api.require_version(session, ri.version_id)
        total += sum(int(s["rows"]) for s in version.splits if ri.split in (None, s["name"]))
    return total


async def _run(
    kind: str,
    version_id: str,
    inputs: list[ReportInput],
    params: dict[str, Any],
    actor: Actor,
    db: AsyncSession,
    *,
    force_job: bool = False,
) -> JSONResponse:
    who = await resolve_who(actor, db)

    def decide(session: Session) -> tuple[int, dict[str, Any]]:
        version = curation_api.require_version(session, version_id)
        existing = report_service.find_completed(session, kind, inputs, params)
        if existing is not None:
            return 200, {"outcome": "existing", "report": report_out(existing)}
        running = report_service.running_for(session, kind, inputs, params)
        if running is not None:
            return 202, {"outcome": "running", "job_id": running.job_id, "report_id": running.id}
        compute = guarded(compute_for(kind))
        rows = _rows(session, inputs)
        if not force_job and rows <= get_settings().curation_inline_max_rows:
            _, report = report_service.find_or_run_inline(
                kind,
                inputs,
                params,
                int(version.seed),
                compute,
                started_by=who.who,
                origin=who.origin,
            )
            return 200, {"outcome": "inline", "report": report_out(report)}
        job, report = report_service.start_job(
            session,
            kind,
            inputs,
            params,
            int(version.seed),
            started_by=who.who,
            origin=who.origin,
        )
        dispatch_queued(session)
        return 202, {"outcome": "started", "job_id": job.id, "report_id": report.id}

    status, body = await _sync(decide)
    return JSONResponse(status_code=status, content=body)


def _latest(kind: str, version_id: str, missing: str, where: Any = None) -> Any:
    def read(session: Session) -> Any:
        curation_api.require_version(session, version_id)
        report = report_service.latest_completed(session, version_id, kind, where=where)
        if report is None:
            raise CurationError(
                missing,
                f"No {kind.replace('_', ' ')} has been run for this version yet. Run it first.",
                {"version_id": version_id},
            )
        return report

    return read


# --- a check over a whole version (ENH-006 D116 G8) ---------------------------------------------


@router.post("/versions/{version_id}/checks", status_code=202)
async def run_version_check(
    version_id: str,
    body: VersionCheckRun,
    actor: Actor = Depends(get_actor),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Start a check operator over every row of the version (or one split): a job whose result is
    ``pass`` or the refusal with its code. Reads rows, calls no model and writes nothing."""
    from ....operators.errors import OperatorError
    from ....operators.registry import current
    from ....operators.version_check import require_check

    who = await resolve_who(actor, db)
    reg = current()
    operator_version = body.version or await run_in_threadpool(reg.current_version, body.name)
    if operator_version is None:
        raise OperatorError(
            "operator_not_found",
            f"No runnable version of {body.name}.",
            {"operator": body.name},
        )
    entry = await run_in_threadpool(reg.require_allowed, body.name, operator_version)
    require_check(entry)
    await run_in_threadpool(reg.require_valid_params, body.name, operator_version, body.params)

    def start(session: Session) -> Job:
        version = curation_api.require_version(session, version_id)
        names = [s["name"] for s in version.splits]
        if body.split is not None and body.split not in names:
            raise AppError(
                f"Version {version.number} has no split {body.split!r}; it has {names}.",
                code="split_not_found",
                status_code=404,
                details={"split": body.split, "splits": names},
            )
        job = Job(
            id=new_id("job"),
            kind="version_check",
            status="queued",
            progress=0.0,
            params={
                "version_id": version.id,
                "split": body.split,
                "operator": body.name,
                "operator_version": operator_version,
                "operator_params": body.params,
            },
            started_by=who.who,
            started_by_origin=who.origin,
        )
        session.add(job)
        session.commit()
        dispatch_queued(session)
        return job

    job = await _sync(start)
    return {
        "job_id": job.id,
        "room": get_job_kind("version_check").room(job.id),
        "operator": entry.ref,
        "next": next_for("dataworks_job_status", {"job_id": job.id}, "read the verdict"),
    }


# --- shortcut audit ---------------------------------------------------------------------------


@router.post("/versions/{version_id}/shortcut-audit")
async def run_shortcut_audit(
    version_id: str,
    body: AuditRun = Body(default_factory=AuditRun),
    detail: Literal["full", "summary"] = Query("full"),
    actor: Actor = Depends(get_actor),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Run (or read back) the shortcut audit. ``detail=summary`` drops each column's per-value
    table and bin edges, keeping every figure, control and verdict (e2e run G9)."""
    inputs = _inputs(version_id, body.inputs)

    def label(session: Session) -> str:
        from ....services.curation import label_columns

        # A malformed path id is 404 version_not_found, as on the GET (it reached the label
        # column query as a UUID bind and answered 500 before).
        curation_api.require_version(session, inputs[0].version_id)
        resolved = label_columns.resolve(session, inputs[0].version_id, body.label_column)
        if resolved.label is None:
            raise CurationError(
                "no_label_column",
                "This version has no label column: no labeling step wrote one and none was "
                "chosen. Add a labeling step (feature 005), or choose a categorical column.",
                {"version_id": version_id},
            )
        return resolved.label

    if (body.group_folds_by or body.reference_version_id) and not body.text_baseline:
        raise CurationError(
            "text_baseline_required",
            "group_folds_by and reference_version_id measure the text baseline: ask for it too "
            "(text_baseline: true).",
            {
                "next": {
                    "tool": "dataworks_run_shortcut_audit",
                    "args": {"version_id": version_id, "text_baseline": True},
                    "why": "run the audit with the text baseline",
                }
            },
        )
    params = curation_api.audit_params(
        await _sync(label),
        text_baseline=body.text_baseline,
        group_folds_by=body.group_folds_by,
        reference_version_id=body.reference_version_id,
    )
    response = await _run("shortcut_audit", version_id, inputs, params, actor, db)
    if detail == "summary":
        import json

        from ....core.compact import summarise_audit

        data = json.loads(bytes(response.body))
        if isinstance(data.get("report"), dict):
            data["report"]["result"] = summarise_audit(data["report"].get("result"))
        return JSONResponse(status_code=response.status_code, content=data)
    return response


@router.get("/versions/{version_id}/shortcut-audit")
async def read_shortcut_audit(
    version_id: str,
    label_column: str | None = Query(default=None),
    detail: Literal["full", "summary"] = Query("full"),
) -> dict[str, Any]:
    where = (lambda r: r.params.get("label_column") == label_column) if label_column else None

    def read(session: Session) -> dict[str, Any]:
        report = _latest("shortcut_audit", version_id, "audit_not_run", where)(session)
        version = curation_api.require_version(session, version_id)
        level = level_service.effective_level(session, version.dataset_id)
        warnings, _, invalid = curation_api.evaluate_audit(version.id, report.result, level)
        if detail == "summary":
            from ....core.compact import summarise_audit

            audit = summarise_audit(report.result)
        else:
            audit = report.result
        return {
            "audit": audit,
            "report": report_out(report) | {"result": None},
            "warnings": [w.model_dump() for w in warnings],
            "invalid": invalid,
            "level": level.as_dict(),
        }

    result: dict[str, Any] = await _sync(read)
    return result


@router.get("/versions/{version_id}/shortcut-audit/cells")
async def read_cell_samples(
    version_id: str,
    column: str = Query(min_length=1),
    value: str = Query(),
    label: str = Query(min_length=1),
    page: int = Query(default=0, ge=0),
    limit: int = Query(default=30, ge=1, le=PAGE_MAX),
) -> dict[str, Any]:
    def read(session: Session) -> dict[str, Any]:
        from ....services.curation import audit_service

        report = _latest("shortcut_audit", version_id, "audit_not_run")(session)
        try:
            return audit_service.cell_samples(
                session,
                report,
                column,
                value,
                label,
                page=page,
                limit=limit,
                sample_size=get_settings().curation_cell_sample_size,
            )
        except audit_service.AuditRefusal as exc:
            raise CurationError(exc.code, exc.message, exc.details) from None

    result: dict[str, Any] = await _sync(read)
    return result


# --- profile, leakage, contamination, TRL, benchmarks ----------------------------------------


@router.post("/versions/{version_id}/profile")
async def run_profile(
    version_id: str,
    body: ProfileRun = Body(default_factory=ProfileRun),
    actor: Actor = Depends(get_actor),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    from ....services.curation.profile_service import embedding_identity

    # 004 FTASKS 15.5: the embeddings endpoint is part of the profile's identity, so configuring
    # (or changing) it computes a new profile instead of finding the old one.
    params = {
        "sample_size": body.sample_size,
        "embeddings": await _sync(embedding_identity),
        "content_read": TEXT_CONTENT,
    }
    return await _run("profile", version_id, [ReportInput(version_id)], params, actor, db)


@router.get("/versions/{version_id}/profile")
async def read_profile(version_id: str) -> dict[str, Any]:
    report = await _sync(_latest("profile", version_id, "profile_not_run"))
    return report_out(report)


@router.post("/versions/{version_id}/leakage")
async def run_leakage(
    version_id: str,
    body: LeakageRun = Body(default_factory=LeakageRun),
    actor: Actor = Depends(get_actor),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    from ....services.curation.leakage_service import leakage_params

    inputs = _inputs(version_id, body.inputs)
    params = leakage_params(body.group_column, body.threshold)
    return await _run("leakage", version_id, inputs, params, actor, db)


@router.get("/versions/{version_id}/leakage")
async def read_leakage(version_id: str) -> dict[str, Any]:
    report = await _sync(_latest("leakage", version_id, "leakage_not_run"))
    return report_out(report)


@router.get("/versions/{version_id}/leakage/pairs")
async def read_leakage_pairs(
    version_id: str,
    page: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=PAGE_MAX),
) -> dict[str, Any]:
    from ....services.curation.leakage_service import read_pairs

    report = await _sync(_latest("leakage", version_id, "leakage_not_run"))
    result: dict[str, Any] = read_pairs(report, page=page, limit=limit)
    return result


@router.post("/versions/{version_id}/contamination")
async def run_contamination(
    version_id: str,
    body: ContaminationRun,
    actor: Actor = Depends(get_actor),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    from ....services.curation.contamination_service import DEFAULT_N, load_benchmark

    n = body.n or DEFAULT_N

    def check(session: Session) -> None:  # refuse an unpinned benchmark BEFORE any job starts
        for source_id in body.benchmark_source_ids:
            load_benchmark(session, source_id, n)

    await _sync(check)
    params = {
        "benchmark_source_ids": sorted(body.benchmark_source_ids),
        "n": n,
        "content_read": TEXT_CONTENT,
    }
    return await _run(
        "contamination", version_id, [ReportInput(version_id)], params, actor, db, force_job=False
    )


@router.get("/versions/{version_id}/contamination")
async def read_contamination(version_id: str) -> dict[str, Any]:
    report = await _sync(_latest("contamination", version_id, "contamination_not_run"))
    return report_out(report)


@router.post("/versions/{version_id}/trl-validation")
async def run_trl_validation(version_id: str, body: TrlRun) -> dict[str, Any]:
    def run(session: Session) -> dict[str, Any]:
        result: dict[str, Any] = curation_api.validate_trl(
            version_id, body.target_type, session=session
        ).model_dump()
        return result

    result: dict[str, Any] = await _sync(run)
    return result


@router.get("/curation/benchmarks")
async def read_benchmarks(paging: OptionalPage = Depends(optional_paging)) -> dict[str, Any]:
    from ....services.curation.contamination_service import catalogue

    items, meta = paging.cut(list(await _sync(catalogue)))
    return {"items": items, **meta}


# --- warning levels ---------------------------------------------------------------------------


def _level_view(session: Session, dataset_id: str | None) -> dict[str, Any]:
    if dataset_id is not None:
        level_service.require_dataset(session, dataset_id)
        level = level_service.effective_level(session, dataset_id)
        return {
            "effective_margin_pp": level.margin_pp,
            "source": level.source,
            "level": level.as_dict(),
            "history": level_service.history(session, dataset_id),
        }
    level = level_service.global_level(session)
    return {
        "margin_pp": level.margin_pp,
        "source": "code_default" if level.source == "code_default" else "set",
        "level": level.as_dict(),
        "history": level_service.history(session, None),
    }


@router.get("/datasets/{dataset_id}/shortcut-level")
async def read_dataset_level(dataset_id: str) -> dict[str, Any]:
    result: dict[str, Any] = await _sync(lambda s: _level_view(s, dataset_id))
    return result


def _require_agent_reason(values: dict[str, Any], actor: Actor) -> None:
    """An agent's level request names its reason before anything is queued (G30)."""
    body = values.get("body")
    if not (getattr(body, "reason", None) or "").strip():
        raise AppError(
            "A request to change a warning level needs a reason: the operator reads it before "
            "approving, and it is kept in the level's history.",
            code="override_reason_required",
            status_code=422,
        )


def _level_summary(payload: dict[str, Any]) -> str:
    body = payload.get("body") or {}
    where = (
        f"dataset {payload['dataset_id']}" if payload.get("dataset_id") else "the global default"
    )
    if body.get("margin_pp") is None:
        return f"Clear the shortcut warning level of {where}: {body.get('reason')}"
    return (
        f"Set the shortcut warning level of {where} to {body['margin_pp']} percentage points: "
        f"{body.get('reason')}"
    )


async def _level_author(actor: Actor, db: AsyncSession, reason: str | None) -> tuple[str, str, str]:
    """(set_by, origin, reason). An approved agent request is the OPERATOR's change: the person
    who approved it, origin operator, the agent and approval named in the reason (G30)."""
    if actor.approval_id is None:
        who = await resolve_who(actor, db)
        return who.who, who.origin, reason or ""
    from ....models.approval import Approval

    approval = await db.get(Approval, actor.approval_id)
    approver = approval.decided_by if approval is not None and approval.decided_by else None
    if approver is None:
        raise AppError(
            "The approval behind this level change names no operator.",
            code="approval_mismatch",
            status_code=409,
        )
    return (
        approver,
        "operator",
        f"{reason or ''} (requested by {actor.agent}, approval {actor.approval_id})".strip(),
    )


@router.put("/datasets/{dataset_id}/shortcut-level", status_code=201)
@requires_approval_when_agent(
    "warning_level_write", before=_require_agent_reason, summary=_level_summary
)
async def set_dataset_level(
    dataset_id: str,
    body: LevelSet,
    actor: Actor = Depends(get_actor),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    set_by, origin, reason = await _level_author(actor, db, body.reason)

    def write(session: Session) -> dict[str, Any]:
        level_service.set_level(
            session,
            dataset_id=dataset_id,
            margin_pp=body.margin_pp,
            reason=reason,
            set_by=set_by,
            origin=origin,
        )
        return _level_view(session, dataset_id)

    result: dict[str, Any] = await _sync(write)
    return result


@router.delete("/datasets/{dataset_id}/shortcut-level", status_code=201)
@requires_approval_when_agent(
    "warning_level_write", before=_require_agent_reason, summary=_level_summary
)
async def clear_dataset_level(
    dataset_id: str,
    body: LevelClear = Body(default_factory=LevelClear),
    actor: Actor = Depends(get_actor),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    set_by, origin, reason = await _level_author(actor, db, body.reason)

    def write(session: Session) -> dict[str, Any]:
        level_service.clear_level(
            session,
            dataset_id=dataset_id,
            reason=reason,
            set_by=set_by,
            origin=origin,
        )
        return _level_view(session, dataset_id)

    result: dict[str, Any] = await _sync(write)
    return result


@router.get("/settings/shortcut-level")
async def read_global_level() -> dict[str, Any]:
    result: dict[str, Any] = await _sync(lambda s: _level_view(s, None))
    return result


@router.put(
    "/settings/shortcut-level", status_code=201, dependencies=[SETTINGS_PIN]
)  # 014: a Settings write
@requires_approval_when_agent(
    "warning_level_write", before=_require_agent_reason, summary=_level_summary
)
async def set_global_level(
    body: LevelSet,
    actor: Actor = Depends(get_actor),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    set_by, origin, reason = await _level_author(actor, db, body.reason)

    def write(session: Session) -> dict[str, Any]:
        level_service.set_level(
            session,
            dataset_id=None,
            margin_pp=body.margin_pp,
            reason=reason,
            set_by=set_by,
            origin=origin,
        )
        return _level_view(session, None)

    result: dict[str, Any] = await _sync(write)
    return result
