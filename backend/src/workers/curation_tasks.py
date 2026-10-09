"""Feature 004's Celery tasks (FTDD 004 §2.2, §7.1; FTID §3.3). Queue ``curation``.

- ``midataworks.curation.run_report`` — a ``curation_report`` job: computes the report its
  ``running`` row names, then completes the row (the immutability trigger allows exactly
  running -> completed). Progress is recorded on time (Foundation's ``record_progress``) and
  cancellation is polled before the work and again before the result is stored, so a cancelled job
  never stores a completed report. Socket.IO room ``dataworks/curation-reports/{job_id}``, events
  ``progress``, ``completed``, ``failed``.
- ``midataworks.curation.post_version`` — sent by feature 002's finalize after a version commits:
  runs the shortcut audit when the version has a label column, and the leakage check (with the
  split step's group column) when its last step was a split (FR-004.36, 004.50).
- ``midataworks.curation.sweep_unaudited`` — Beat: finds completed versions whose steps include a
  labeler or a split and that have no audit report yet, and sends ``post_version`` for each (at most
  50 per run). It is the backstop for a lost message, not the mechanism.

A post-build audit that refuses (one class, too few rows) stores a ``failed`` report row with the
refusal, so the sweeper does not resend it every minute and the version shows why it has no audit.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from ..core.cancellation import CancelCheck, cooperative_cancel, record_progress
from ..core.celery_app import celery_app
from ..core.clock import utc_now
from ..core.database import get_sync_db
from ..core.job_kinds import get_job_kind
from ..models.curation import ReportState, VersionReport
from ..models.enums import VersionState
from ..models.recipe import RecipeBody
from ..models.step_execution import StepExecution
from ..models.version import Version, VersionStep
from ..services.curation import api as curation_api
from ..services.curation import label_columns, report_service
from ..services.curation.audit_service import AuditRefusal, compute_audit
from ..services.curation.codes import ReportInput, as_inputs
from ..services.curation.errors import CurationError
from ..services.curation.kinds import compute_for
from ..services.health_service import CURATION_SWEEPER_KEY as SWEEPER_KEY
from ..services.job_service import claim_job, dispatch_queued
from .emit import emit
from .janitor import register_on_reap

logger = logging.getLogger(__name__)

POST_VERSION = "midataworks.curation.post_version"
SWEEP_LIMIT = 50
SWEEP_SCAN = 500
SYSTEM = "midataworks"


class Heartbeat:
    """A time-throttled sign of life while one long compute call runs (ADR-007).

    A report's compute is a single call, so nothing inside it reaches a progress checkpoint; this
    thread writes ``record_progress`` every ``PROGRESS_HEARTBEAT_SECONDS`` so the janitor never
    takes a live 20-minute report for a dead one (miStudio reaped a live 5.8-hour job that way).
    """

    def __init__(self, job_id: str, message: str) -> None:
        import threading

        self.job_id = job_id
        self.message = message
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._beat, daemon=True)
        self.beats = 0

    def _beat(self) -> None:
        from ..core.config import get_settings

        interval = float(get_settings().progress_heartbeat_seconds)
        while not self._stop.wait(interval):
            record_progress(self.job_id, message=self.message, force=True)
            self.beats += 1

    def __enter__(self) -> Heartbeat:
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._stop.set()
        self._thread.join(timeout=5)


def _room(job_id: str) -> str:
    return get_job_kind("curation_report").room(job_id)


def _send_task(name: str, args: list[Any]) -> None:
    celery_app.send_task(name, args=args)


def _next_jobs() -> None:
    try:
        with get_sync_db() as db:
            dispatch_queued(db)
    except Exception as exc:  # noqa: BLE001 - Beat dispatches again
        logger.warning("Could not dispatch the next queued job: %s", exc)


@celery_app.task(name="midataworks.curation.run_report", acks_late=True)
def run_report(job_id: str) -> dict[str, Any]:
    try:
        return _run_report(job_id)
    finally:
        _next_jobs()


def _finish_report(report_id: str, state: str, **values: Any) -> None:
    with get_sync_db() as db:
        row = db.get(VersionReport, report_id)
        if row is None or row.state != ReportState.RUNNING:
            return
        row.state = state
        for name, value in values.items():
            setattr(row, name, value)
        db.commit()


@cooperative_cancel
def _run_report(job_id: str) -> dict[str, Any]:
    with get_sync_db() as db:
        job = claim_job(db, job_id)
        if job is None:
            return {"skipped": "not claimable"}
        report_id = str(job.params["report_id"])
        report = db.get(VersionReport, report_id)
        if report is None:
            raise CurationError("report_not_found", f"Job {job_id} names no report row.")
        kind, inputs, params, seed = (
            report.kind,
            as_inputs(report.inputs),
            dict(report.params),
            int(report.seed),
        )
    cancel = CancelCheck(job_id)
    started = time.monotonic()
    emit(_room(job_id), "progress", {"job_id": job_id, "report_id": report_id, "stage": "started"})
    record_progress(job_id, progress=1.0, message=f"Computing the {kind} report", force=True)
    try:
        cancel.raise_if_cancelled("before the report was computed")
        with get_sync_db() as db, Heartbeat(job_id, f"Computing the {kind} report"):
            computed = compute_for(kind)(db, inputs, params, seed)
        cancel.raise_if_cancelled("the report was computed and is not stored")
    except BaseException as exc:
        from ..core.cancellation import OperatorCancelled

        if isinstance(exc, OperatorCancelled):
            _finish_report(report_id, ReportState.CANCELLED, error={"code": "cancelled"})
            raise
        error = {
            "code": getattr(exc, "code", type(exc).__name__),
            "message": getattr(exc, "message", str(exc)),
        }
        _finish_report(report_id, ReportState.FAILED, error=error)
        record_progress(job_id, status="failed", error=error["message"])
        emit(_room(job_id), "failed", {"job_id": job_id, "report_id": report_id, "error": error})
        logger.info("report failed kind=%s report=%s code=%s", kind, report_id, error["code"])
        return {"failed": error}
    _finish_report(
        report_id,
        ReportState.COMPLETED,
        result=computed.result,
        artefacts=computed.artefacts,
        completed_at=utc_now(),
    )
    record_progress(job_id, status="completed", progress=100.0, result={"report_id": report_id})
    emit(_room(job_id), "completed", {"job_id": job_id, "report_id": report_id, "kind": kind})
    logger.info(
        "report end kind=%s version=%s rows=%d seconds=%.2f params=%s",
        kind,
        inputs[0].version_id,
        computed.rows,
        time.monotonic() - started,
        report_service.params_hash(params)[:12],
    )
    return {"report_id": report_id}


def _store_refusal(
    version: Version, params: dict[str, Any], exc: AuditRefusal | CurationError
) -> None:
    name, ver, digest = report_service.identity_of("shortcut_audit")
    inputs = [ReportInput(version.id)]
    with get_sync_db() as db:
        db.add(
            VersionReport(
                id=str(uuid.uuid4()),
                version_id=version.id,
                kind="shortcut_audit",
                operator_name=name,
                operator_version=ver,
                manifest_hash=digest,
                params_hash=report_service.params_hash(params),
                params=params,
                inputs=report_service.inputs_document(inputs),
                inputs_digest=report_service.inputs_digest(inputs),
                seed=int(version.seed),
                state=ReportState.FAILED,
                error={"code": exc.code, "message": exc.message},
                started_by=SYSTEM,
                started_by_origin="operator",
            )
        )
        db.commit()


def last_split_group_column(session: Session, version: Version) -> tuple[bool, str | None]:
    """Whether the version's LAST operator step is the split, and its group column."""
    last = session.execute(
        select(VersionStep.step_index, StepExecution.operator_name)
        .join(StepExecution, StepExecution.id == VersionStep.step_execution_id)
        .where(VersionStep.version_id == version.id, StepExecution.kind == "operator")
        .order_by(VersionStep.step_index.desc())
        .limit(1)
    ).first()
    if last is None or last[1] != "split":
        return False, None
    body = session.get(RecipeBody, version.recipe_hash)
    steps = (body.body if body else {}).get("steps", [])
    index = int(last[0]) - 1  # step 0 is assemble; recipe steps start at index 1
    params = steps[index].get("params", {}) if 0 <= index < len(steps) else {}
    return True, params.get("group_column")


@celery_app.task(name="midataworks.curation.post_version", acks_late=True)
def post_version(version_id: str) -> dict[str, Any]:
    """Audit (and, after a split, check leakage of) a committed version (FR-004.36, 004.50)."""
    out: dict[str, Any] = {"version_id": version_id}
    with get_sync_db() as db:
        version = db.get(Version, version_id)
        if version is None or version.state != VersionState.COMPLETED:
            return {**out, "skipped": "not a completed version"}
        labels = label_columns.resolve(db, version_id)
        split_last, group_column = last_split_group_column(db, version)
    if labels.label is not None:
        params = curation_api.audit_params(labels.label)
        try:
            outcome, report = report_service.find_or_run_inline(
                "shortcut_audit",
                [ReportInput(version_id)],
                params,
                int(version.seed),
                compute_audit,
                started_by=SYSTEM,
                origin="operator",
            )
            out["audit"] = {"outcome": outcome, "report_id": report.id}
        except (AuditRefusal, CurationError) as exc:
            _store_refusal(version, params, exc)
            out["audit"] = {"refused": exc.code}
    if split_last:
        with get_sync_db() as db:
            result = curation_api.check_leakage([version_id], group_column=group_column, session=db)
        out["leakage"] = {"report_id": result.report_id, "pairs": result.total}
    return out


def unaudited(session: Session, limit: int = SWEEP_LIMIT, registry: Any = None) -> list[str]:
    """Completed versions with a labeler or split step and no audit row of any state."""
    from ..operators.registry import current

    reg = registry if registry is not None else current()
    candidates = session.execute(
        select(Version.id)
        .where(
            Version.state == VersionState.COMPLETED,
            ~exists().where(
                VersionReport.version_id == Version.id, VersionReport.kind == "shortcut_audit"
            ),
        )
        .order_by(Version.created_at.desc())
        .limit(SWEEP_SCAN)
    ).scalars()
    out: list[str] = []
    for vid in candidates:
        names = session.execute(
            select(StepExecution.operator_name, StepExecution.operator_version)
            .join(VersionStep, VersionStep.step_execution_id == StepExecution.id)
            .where(VersionStep.version_id == vid, StepExecution.kind == "operator")
        ).all()
        wanted = False
        for name, ver in names:
            if name == "split":
                wanted = True
                break
            try:
                if reg.get(str(name), str(ver)).kind == "labeler":
                    wanted = True
                    break
            except Exception as exc:  # noqa: BLE001 - an uninstalled operator is not a labeler
                logger.debug("sweep: %s@%s not readable: %s", name, ver, exc)
        if wanted:
            out.append(str(vid))
        if len(out) >= limit:
            break
    return out


def fail_orphaned_reports(session: Session) -> int:
    """A ``running`` report whose job is terminal (the janitor failed a dead worker's job) is failed
    too, so no report stays ``running`` forever and a repeat run can start (FTASKS 11.4)."""
    from ..models.job import TERMINAL_STATUSES, Job

    rows = (
        session.execute(
            select(VersionReport)
            .join(Job, Job.id == VersionReport.job_id)
            .where(
                VersionReport.state == ReportState.RUNNING, Job.status.in_(tuple(TERMINAL_STATUSES))
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        row.state = ReportState.FAILED
        row.error = {"code": "job_ended", "message": "The report's job ended before it completed."}
    session.commit()
    return len(rows)


def _on_reap(session: Session, job_id: str, status: str, reason: str) -> None:
    """When the janitor reaps a dead worker's report job, its ``running`` row fails with the job."""
    rows = session.execute(
        select(VersionReport).where(
            VersionReport.job_id == job_id, VersionReport.state == ReportState.RUNNING
        )
    ).scalars()
    for row in rows:
        row.state = ReportState.FAILED if status == "failed" else ReportState.CANCELLED
        row.error = {"code": "job_reaped", "message": reason}
    session.commit()


register_on_reap("curation_report", _on_reap)


@celery_app.task(name="midataworks.curation.sweep_unaudited")
def sweep_unaudited() -> dict[str, Any]:
    with get_sync_db() as db:
        fail_orphaned_reports(db)
        ids = unaudited(db)
    for vid in ids:
        _send_task(POST_VERSION, [vid])
    _stamp_sweeper()
    return {"enqueued": ids}


def _stamp_sweeper() -> None:
    try:
        import redis

        from ..core.config import get_settings

        client = redis.Redis.from_url(get_settings().redis_url)
        client.set(SWEEPER_KEY, utc_now().isoformat())
    except Exception as exc:  # noqa: BLE001 - health narration must not break the sweep
        logger.warning("Could not record the sweeper's run: %s", exc)
