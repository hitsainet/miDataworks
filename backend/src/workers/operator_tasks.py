"""Celery tasks for feature 003 (FR-003.17, FR-003.19; FTDD 003 section 6.1; FTID 003 section 3.6).

- ``midataworks.operators.step.curation`` / ``midataworks.operators.step.labeling`` — run one step
  in this process (``executor.execute_in_process``). Queues ``curation`` and ``labeling``.
- ``midataworks.operators.step.finalize_datajuicer`` — turn the Data-Juicer runner's raw decisions
  into events, check conservation, publish, then send feature 002's callback. Queue ``curation``.
- ``midataworks.operators.preview`` — a preview or a statistics call; results live in Redis only.
  Queue ``preview``.

A step that violates the contract publishes ONLY a ``meta.json`` carrying the error; the task then
returns normally so the Celery ``link`` reaches feature 002's ``advance_build``, which fails the step
with the code (FTASKS 6.7).

**Cancellation (deviation, recorded).** FTASKS 6.4 named Foundation's ``cooperative_cancel``
wrapper. That wrapper marks the JOB cancelled itself; the job here is feature 002's build, whose
own ``advance`` pass then sees a terminal row, skips, and never runs its cleanup — leaving the step
execution ``running`` and its output directory behind. So the step task catches
``OperatorCancelled`` itself, keeps the completed batches in ``staging/``, and returns; the link
reaches ``advance``, which sees the cancel request and runs 002's cancel path (which marks the job).
"""

from __future__ import annotations

import logging
from typing import Any

from celery.signals import worker_init, worker_process_init

from ..core.cancellation import CancelCheck, OperatorCancelled, cooperative_cancel, record_progress
from ..core.celery_app import celery_app
from ..core.config import get_settings
from ..core.database import get_sync_db
from ..core.storage import resolve_under_data_dir
from ..models.enums import VersionState
from ..models.version import Version
from ..operators import executor, preview, version_check
from ..operators.errors import OperatorError
from ..operators.registry import current as registry_current
from ..services.job_service import claim_job, dispatch_queued
from ..services.operator_port import StepSpec

logger = logging.getLogger(__name__)


def run_step(payload: dict[str, Any]) -> dict[str, Any]:
    spec = StepSpec(**payload)
    try:
        result = executor.execute_in_process(spec)
    except OperatorCancelled as cancelled:
        logger.info("step %s stopped: %s", spec.step_execution_id, cancelled.reason)
        return {"status": "cancelled", "step_execution_id": spec.step_execution_id}
    except OperatorError as error:
        logger.warning("step %s failed: %s %s", spec.step_execution_id, error.code, error.message)
        executor.write_failure(spec.output_dir, error)
        return {"status": "failed", "code": error.code}
    except Exception as exc:  # noqa: BLE001 - reported to 002 as a failed step, never swallowed
        logger.exception("step %s failed unexpectedly", spec.step_execution_id)
        executor.write_failure(
            spec.output_dir,
            {
                "code": "step_failed",
                "message": f"The operator raised {type(exc).__name__}: {exc}"[:2000],
                "details": {"exception": type(exc).__name__},
            },
        )
        return {"status": "failed", "code": "step_failed"}
    return {
        "status": "completed",
        "step_execution_id": spec.step_execution_id,
        "rows_in": result.rows_in,
        "rows_dropped": result.rows_dropped,
        "pinned": result.pinned,
    }


@celery_app.task(name="midataworks.operators.run_version_check", acks_late=True)
def run_version_check(job_id: str) -> dict[str, Any]:
    """A check operator over every row of a version (ENH-006 D116 G8). A refusal is the check's
    answer, so the job completes with it; only a fault fails the job."""
    try:
        return _run_version_check(job_id)
    finally:
        try:
            with get_sync_db() as db:
                dispatch_queued(db)
        except Exception as exc:  # noqa: BLE001 - Beat dispatches again
            logger.warning("Could not dispatch the next queued job: %s", exc)


@cooperative_cancel
def _run_version_check(job_id: str) -> dict[str, Any]:
    with get_sync_db() as db:
        job = claim_job(db, job_id)
        if job is None:
            return {"skipped": "not claimable"}
        asked = dict(job.params)
        version = db.get(Version, str(asked["version_id"]))
        if version is None or version.state != VersionState.COMPLETED:
            record_progress(job_id, status="failed", error="The version is gone.")
            return {"failed": "version_gone"}
        chosen = [s for s in version.splits if asked.get("split") in (None, s["name"])]
        files = [resolve_under_data_dir(s["path"]) for s in chosen]
        expected_rows = sum(int(s["rows"]) for s in chosen)
        roles, scheme, seed = dict(version.column_roles), version.rowkey_scheme, int(version.seed)
    label = f"Checking {asked['operator']}@{asked['operator_version']}"
    cancel = CancelCheck(job_id)
    record_progress(job_id, progress=1.0, message=label, force=True)
    try:
        from .curation_tasks import Heartbeat

        with Heartbeat(job_id, label):
            answer = version_check.run_check(
                registry_current(),
                operator=str(asked["operator"]),
                version=str(asked["operator_version"]),
                params=dict(asked["operator_params"]),
                files=files,
                expected_rows=expected_rows,
                column_roles=roles,
                rowkey_scheme=str(scheme),
                seed=seed,
                job_id=job_id,
                check_cancel=lambda: cancel.raise_if_cancelled(
                    "the check stopped; it wrote nothing"
                ),
            )
    except (
        Exception
    ) as exc:  # noqa: BLE001 - a fault fails the job; a refusal came back as the answer
        logger.exception("version check %s failed", job_id)
        record_progress(job_id, status="failed", error=f"{type(exc).__name__}: {exc}"[:2000])
        return {"failed": type(exc).__name__}
    result = {
        "version_id": str(asked["version_id"]),
        "split": asked.get("split"),
        "rows_in_scope": expected_rows,
        **answer,
    }
    record_progress(job_id, status="completed", progress=100.0, result=result)
    return {"job_id": job_id, "verdict": answer["verdict"]}


@celery_app.task(name="midataworks.operators.step.curation", acks_late=True)
def step_curation(payload: dict[str, Any]) -> dict[str, Any]:
    return run_step(payload)


@celery_app.task(name="midataworks.operators.step.labeling", acks_late=True)
def step_labeling(payload: dict[str, Any]) -> dict[str, Any]:
    return run_step(payload)


@celery_app.task(name="midataworks.operators.step.finalize_datajuicer", acks_late=True)
def finalize_datajuicer(
    payload: dict[str, Any], link_task: str, link_args: list[Any], failed: bool = False
) -> dict[str, Any]:
    from ..operators.datajuicer.finalize import finalize_step

    spec = StepSpec(**payload)
    try:
        outcome = finalize_step(spec, failed=failed)
    finally:
        executor.send_task(link_task, args=list(link_args))
    return outcome


@celery_app.task(name="midataworks.operators.step.finalize_designer", acks_late=True)
def finalize_designer(
    payload: dict[str, Any], link_task: str, link_args: list[Any], failed: bool = False
) -> dict[str, Any]:
    from ..operators.data_designer.finalize import finalize_step

    spec = StepSpec(**payload)
    try:
        outcome = finalize_step(spec, failed=failed)
    finally:
        executor.send_task(link_task, args=list(link_args))
    return outcome


@celery_app.task(
    name="midataworks.operators.preview",
    acks_late=False,
    time_limit=int(get_settings().operator_preview_hard_limit_s),
)
def run_preview_task(*args: Any) -> dict[str, Any]:
    """``(request)`` for a native preview; ``(runner_result, request)`` when linked after the
    Data-Juicer runner's preview (Celery prepends the parent's result)."""
    if len(args) == 2:
        raw, request = args
        return preview.run_request(request, raw)
    return preview.run_request(args[0])


@celery_app.task(name="midataworks.operators.preview_failed", acks_late=False)
def preview_failed(*args: Any) -> dict[str, Any]:
    """Store a preview whose task failed (PII run G3/F-1, 2026-10-20), so the poll answers
    ``{status: failed, error}`` with the worker's own message.

    Linked as ``link_error`` with ``(preview_id, code)``. Celery calls it two ways: a worker that
    does not have this task registered (the Data Designer and Data-Juicer images) SENDS it with
    the failed task's id prepended, ``(task_id, preview_id, code)``, and the failure is read from
    the result backend; a worker that has it (the backend's own preview queue) CALLS it with
    ``(request, exc, traceback, preview_id, code)``.
    """
    preview_id, code = str(args[-2]), str(args[-1])
    if len(args) == 3:
        exc_type, message = preview.failure_of_task(str(args[0]))
    else:
        exc = args[1]
        exc_type, message = type(exc).__name__, str(exc)
    return preview.store_failure(preview_id, code, exc_type, message)


@worker_init.connect
@worker_process_init.connect
def _install_registry(**_: Any) -> None:
    """Build this worker's registry once and install it as 002's port (FTID 003 section 3.4)."""
    from ..operators.registry import install_process_registry

    install_process_registry()
