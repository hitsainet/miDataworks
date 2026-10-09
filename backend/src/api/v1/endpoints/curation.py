"""Feature 004's REST routes (FTDD 004 §5.1; FPRD §7.1).

"Run" routes answer ``200`` with a stored report when an identical one exists, ``200`` after
computing inline when the input is under ``CURATION_INLINE_MAX_ROWS``, or ``202 {job_id}`` with a
``curation_report`` job. Read routes answer ``404 <kind>_not_run`` when nothing is stored.

Level writes depend on :data:`refuse_level_write` FIRST (403 ``agent_forbidden`` before any body
becomes a write, P-09); the table's ``origin = 'operator'`` check is the second wall. No route here
accepts a filesystem path: every path is built from a version id by feature 002's storage helpers.
Operator previews, statistics and recipe builds use features 003's and 002's routes.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Body, Depends, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ....core.agent_origin import Actor, get_actor, refusing_agents, resolve_who
from ....core.config import get_settings
from ....core.database import get_db, sync_session_factory
from ....schemas.curation import (
    AuditRun,
    ContaminationRun,
    InputRef,
    LeakageRun,
    LevelClear,
    LevelSet,
    ProfileRun,
    TrlRun,
)
from ....services.curation import api as curation_api
from ....services.curation import level_service, report_service
from ....services.curation.codes import ReportInput
from ....services.curation.errors import CurationError
from ....services.curation.kinds import compute_for, guarded

router = APIRouter(prefix="/api/v1", tags=["curation"])

refuse_level_write = refusing_agents(
    "Agents can read the shortcut warning levels but never change them; ask the operator to "
    "change the level on the Settings screen or the dataset's page, with a reason (P-09)."
)

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
        from ....services.job_service import dispatch_queued

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


# --- shortcut audit ---------------------------------------------------------------------------


@router.post("/versions/{version_id}/shortcut-audit")
async def run_shortcut_audit(
    version_id: str,
    body: AuditRun = Body(default_factory=AuditRun),
    actor: Actor = Depends(get_actor),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    inputs = _inputs(version_id, body.inputs)

    def label(session: Session) -> str:
        from ....services.curation import label_columns

        resolved = label_columns.resolve(session, inputs[0].version_id, body.label_column)
        if resolved.label is None:
            raise CurationError(
                "no_label_column",
                "This version has no label column: no labeling step wrote one and none was "
                "chosen. Add a labeling step (feature 005), or choose a categorical column.",
                {"version_id": version_id},
            )
        return resolved.label

    params = curation_api.audit_params(await _sync(label))
    return await _run("shortcut_audit", version_id, inputs, params, actor, db)


@router.get("/versions/{version_id}/shortcut-audit")
async def read_shortcut_audit(
    version_id: str, label_column: str | None = Query(default=None)
) -> dict[str, Any]:
    where = (lambda r: r.params.get("label_column") == label_column) if label_column else None

    def read(session: Session) -> dict[str, Any]:
        report = _latest("shortcut_audit", version_id, "audit_not_run", where)(session)
        version = curation_api.require_version(session, version_id)
        level = level_service.effective_level(session, version.dataset_id)
        warnings, _, invalid = curation_api.evaluate_audit(version.id, report.result, level)
        return {
            "audit": report.result,
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
    params = {"sample_size": body.sample_size}
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
    params = {"benchmark_source_ids": sorted(body.benchmark_source_ids), "n": n}
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
async def read_benchmarks() -> dict[str, Any]:
    from ....services.curation.contamination_service import catalogue

    items = await _sync(catalogue)
    return {"items": items}


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


@router.put("/datasets/{dataset_id}/shortcut-level", status_code=201)
async def set_dataset_level(
    dataset_id: str,
    body: LevelSet,
    actor: Actor = Depends(refuse_level_write),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    who = await resolve_who(actor, db)

    def write(session: Session) -> dict[str, Any]:
        level_service.set_level(
            session,
            dataset_id=dataset_id,
            margin_pp=body.margin_pp,
            reason=body.reason or "",
            set_by=who.who,
            origin=who.origin,
        )
        return _level_view(session, dataset_id)

    result: dict[str, Any] = await _sync(write)
    return result


@router.delete("/datasets/{dataset_id}/shortcut-level", status_code=201)
async def clear_dataset_level(
    dataset_id: str,
    body: LevelClear = Body(default_factory=LevelClear),
    actor: Actor = Depends(refuse_level_write),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    who = await resolve_who(actor, db)

    def write(session: Session) -> dict[str, Any]:
        level_service.clear_level(
            session,
            dataset_id=dataset_id,
            reason=body.reason or "",
            set_by=who.who,
            origin=who.origin,
        )
        return _level_view(session, dataset_id)

    result: dict[str, Any] = await _sync(write)
    return result


@router.get("/settings/shortcut-level")
async def read_global_level() -> dict[str, Any]:
    result: dict[str, Any] = await _sync(lambda s: _level_view(s, None))
    return result


@router.put("/settings/shortcut-level", status_code=201)
async def set_global_level(
    body: LevelSet,
    actor: Actor = Depends(refuse_level_write),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    who = await resolve_who(actor, db)

    def write(session: Session) -> dict[str, Any]:
        level_service.set_level(
            session,
            dataset_id=None,
            margin_pp=body.margin_pp,
            reason=body.reason or "",
            set_by=who.who,
            origin=who.origin,
        )
        return _level_view(session, None)

    result: dict[str, Any] = await _sync(write)
    return result
