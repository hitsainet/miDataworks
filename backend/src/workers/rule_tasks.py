"""``midataworks.rules.derive_rule_set``: the rule-derivation job (feature 016; FTID 016 section 3.9).

Routed by EXACT name to the ``curation`` queue (``core/celery_app.TASK_ROUTES``: the
``midataworks.curation.*`` glob does not match ``midataworks.rules.*``). Its soft time limit is the
runtime setting ``RULE_DERIVATION_TIME_LIMIT_S``, applied per dispatch (``JobKind.send_options``),
so a change reaches the next run without a restart (FTID I11).
"""

from __future__ import annotations

import logging
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded

from ..core.cancellation import OperatorCancelled, cooperative_cancel, record_progress
from ..core.celery_app import celery_app
from ..core.database import get_sync_db
from ..core.errors import AppError
from ..services.job_service import claim_job, dispatch_queued
from ..services.rules import derivation_service

logger = logging.getLogger(__name__)

DERIVE_TASK = "midataworks.rules.derive_rule_set"


@cooperative_cancel
def derive_job(job_id: str) -> dict[str, Any]:
    with get_sync_db() as session:
        job = claim_job(session, job_id)
        if job is None:
            return {"job_id": job_id, "outcome": "skipped"}
        derivation_id = str(job.params["derivation_id"])
        try:
            out = derivation_service.run(session, derivation_id, job_id)
        except OperatorCancelled:
            derivation_service.cancelled(session, derivation_id)
            raise
        except SoftTimeLimitExceeded:
            message = "The derivation passed RULE_DERIVATION_TIME_LIMIT_S and was stopped."
            derivation_service.fail(session, derivation_id, "time_limit", message)
            record_progress(
                job_id,
                status="failed",
                error=message,
                result={"error": {"code": "time_limit", "message": message}},
            )
            return {"job_id": job_id, "outcome": "failed"}
        except AppError as exc:
            derivation_service.fail(session, derivation_id, exc.code, exc.message)
            record_progress(
                job_id,
                status="failed",
                error=exc.message,
                result={"error": {"code": exc.code, "message": exc.message}},
            )
            return {"job_id": job_id, "outcome": "failed"}
        except Exception as exc:  # noqa: BLE001 - recorded on the row and the job, never raised
            logger.exception("rule derivation %s failed", derivation_id)
            message = f"The derivation failed ({type(exc).__name__})."
            derivation_service.fail(session, derivation_id, "derivation_failed", message)
            record_progress(
                job_id,
                status="failed",
                error=message,
                result={"error": {"code": "derivation_failed", "message": message}},
            )
            return {"job_id": job_id, "outcome": "failed"}
        record_progress(job_id, status="completed", progress=100.0, result=out)
        return {"job_id": job_id, "outcome": "completed", **out}


@celery_app.task(name=DERIVE_TASK, acks_late=True)
def derive_rule_set(job_id: str) -> dict[str, Any]:
    try:
        return derive_job(job_id)
    finally:
        try:
            with get_sync_db() as db:
                dispatch_queued(db)
        except Exception as exc:  # noqa: BLE001 - Beat dispatches again
            logger.warning("Could not dispatch the next queued job: %s", exc)
