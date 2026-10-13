"""``midataworks.detector_sets.run_mistudio_send`` on the ``publish`` queue (FR-009.17 - FR-009.28;
FTDD 009 sections 2.2, 6.2; FTID 009 section 7.3).

Per send it walks its step rows in order — publish each distinct version, download each
(repository, split) into miStudio, register each role — skipping ``done`` and ``reused`` rows, so a
resumed send never repeats a call (FR-009.27). Each step's result is written in its own
transaction, with a ``record_progress`` heartbeat, an emit to ``dataworks/detector-sends/{id}`` and a
cancel check at the boundary.

**Publishing goes through 008's single entry point.** ``publish_service.request_publish`` with
``SendApproval`` (agent) or ``OperatorOrigin`` creates the publish record and its job; this task then
runs 008's own publish job body (``publish_tasks.run_publish_job``) IN PROCESS. It cannot wait for a
Celery worker to do it: the ``publish`` queue's worker runs one task at a time and this send is that
task, so a dispatched publish would wait behind the send that waits for it. The publish job is
marked dispatched (``celery_task_id``) before it runs, so the dispatcher never sends it too; a
duplicate delivery finds it claimed and does nothing (008's ``_settle_unstarted`` now settles only a
job that was really cancelled — the fix is in this commit).

**Downloads poll** ``GET /datasets/{id}`` every ``DETECTOR_SEND_POLL_SECONDS`` with a
``record_progress`` on EVERY poll: a download that reports only to miStudio looks dead to the janitor
otherwise (miStudio memory "long phases need a DB heartbeat").
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..clients import mistudio_client as mc
from ..core.cancellation import CancelCheck, OperatorCancelled, cooperative_cancel, record_progress
from ..core.canonical_json import canonical_sha256
from ..core.celery_app import celery_app
from ..core.clock import utc_now
from ..core.config import get_settings
from ..core.database import get_sync_db
from ..core.errors import AppError
from ..core.ids import new_id
from ..models.detector_send import DetectorSend, DetectorSendStep, MiStudioRegistration
from ..models.job import Job
from ..models.publish import TERMINAL_PUBLISH_STATUSES, Publish, PublishStatus
from ..services.detector_sets import capabilities, plan
from ..services.job_service import claim_job, dispatch_queued
from ..services.publishing import publish_service
from . import janitor
from .emit import emit

logger = logging.getLogger(__name__)

TASK_NAME = "midataworks.detector_sets.run_mistudio_send"
ROOM = "dataworks/detector-sends/{id}"
#: Test seam: how a client is built for a send's miStudio URL.
CLIENT_FACTORY: Callable[[str], mc.MiStudioClient] = mc.MiStudioClient
#: Test seam: seconds slept between download polls (the setting, unless a test overrides it).
SLEEP: Callable[[float], None] = time.sleep


class StepFailed(Exception):
    def __init__(
        self, code: str, message: str, next_step: str | None = None, **details: Any
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.next_step = next_step
        self.details = details

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "next_step": self.next_step,
            "details": self.details,
        }


def _room(send_id: str) -> str:
    return ROOM.replace("{id}", send_id)


def _next_jobs() -> None:
    try:
        with get_sync_db() as db:
            dispatch_queued(db)
    except Exception as exc:  # noqa: BLE001 - Beat dispatches again
        logger.warning("Could not dispatch the next queued job: %s", exc)


@celery_app.task(name=TASK_NAME, acks_late=True)
def run_mistudio_send(job_id: str) -> dict[str, Any]:
    try:
        return run_job(job_id)
    finally:
        _next_jobs()


# --- the step functions -----------------------------------------------------------------------


def _write(db: Session, step: DetectorSendStep, **values: Any) -> None:
    for key, value in values.items():
        setattr(step, key, value)
    db.commit()


def _unit(send: DetectorSend, kind: str, key: str) -> dict[str, Any]:
    if kind == "publish":
        return next(u for u in send.plan["publish_units"] if u["version_id"] == key)
    if kind == "download":
        return next(u for u in send.plan["download_units"] if u["key"] == key)
    return next(r for r in send.plan["roles"] if r["role_id"] == key)


def _authorization(send: DetectorSend) -> publish_service.Authorization:
    if send.approval_id is not None:
        return publish_service.SendApproval(send.approval_id, send.id)
    return publish_service.OperatorOrigin()


def _wait_for_publish(db: Session, job_id: str, publish_id: str, checker: CancelCheck) -> Publish:
    poll = get_settings().detector_send_poll_seconds
    while True:
        pub = db.get(Publish, publish_id, populate_existing=True)
        assert pub is not None
        if pub.status in TERMINAL_PUBLISH_STATUSES:
            return pub
        record_progress(job_id, message=f"Waiting for publish {publish_id}", force=True)
        checker.raise_if_cancelled("Cancelled while a publish was running.")
        SLEEP(poll)


def do_publish(
    db: Session, send: DetectorSend, step: DetectorSendStep, job_id: str, checker: CancelCheck
) -> str:
    unit = _unit(send, "publish", step.unit_key)
    if unit["reuse_publish_id"]:
        pub = db.get(Publish, unit["reuse_publish_id"])
        assert pub is not None
        _write(db, step, publish_id=pub.id, commit=pub.commit, repo_id=pub.repo_id)
        return "reused"
    pub = db.get(Publish, step.publish_id) if step.publish_id else None
    if pub is None or pub.status in (
        PublishStatus.FAILED,
        PublishStatus.REFUSED,
        PublishStatus.CANCELLED,
        PublishStatus.VERIFICATION_FAILED,
    ):
        req = publish_service.PublishRequest(
            version_id=unit["version_id"],
            build_id=unit["build_id"],
            repo_id=unit["repo_id"],
            visibility=unit["visibility"],
            card_prose=unit["card_prose"],
        )
        try:
            created = publish_service.request_publish(
                db,
                req,
                _authorization(send),
                publish_service.Who(send.started_by, send.started_by_origin),
                send_id=send.id,
            )
        except AppError as exc:
            db.rollback()
            raise StepFailed(exc.code, exc.message, None, **exc.details) from exc
        pub = created.publish
        # Mark it dispatched before running it here, so the dispatcher never sends it as well.
        db.execute(
            update(Job)
            .where(Job.id == pub.job_id, Job.celery_task_id.is_(None))
            .values(celery_task_id=f"inline-{job_id}")
        )
        _write(db, step, publish_id=pub.id, repo_id=pub.repo_id)
    if pub.status not in TERMINAL_PUBLISH_STATUSES:
        from . import publish_tasks

        ran = publish_tasks.run_publish_job(pub.job_id)
        if ran.get("status") == "skipped":
            _wait_for_publish(db, job_id, pub.id, checker)
    pub = db.get(Publish, pub.id, populate_existing=True)
    assert pub is not None
    if pub.status not in (PublishStatus.PUBLISHED, PublishStatus.NO_CHANGE) or not pub.commit:
        error = pub.error or {}
        raise StepFailed(
            "publish_failed",
            f"Publishing version to {pub.repo_id} ended {pub.status}: "
            f"{error.get('message', 'no reason recorded')}",
            "Read the publish record in Publish and export, fix what it names, then resume.",
            publish_id=pub.id,
            status=pub.status,
        )
    _write(db, step, commit=pub.commit)
    return "done"


def _publish_step_for(db: Session, send: DetectorSend, version_id: str) -> DetectorSendStep:
    return db.execute(
        select(DetectorSendStep).where(
            DetectorSendStep.send_id == send.id,
            DetectorSendStep.step == "publish",
            DetectorSendStep.unit_key == version_id,
        )
    ).scalar_one()


def do_download(
    db: Session,
    send: DetectorSend,
    step: DetectorSendStep,
    job_id: str,
    checker: CancelCheck,
    client: mc.MiStudioClient,
    caps: capabilities.Capabilities,
) -> str:
    unit = _unit(send, "download", step.unit_key)
    commit = _publish_step_for(db, send, unit["version_id"]).commit
    assert commit is not None
    known = db.execute(
        select(MiStudioRegistration).where(
            MiStudioRegistration.kind == "dataset",
            MiStudioRegistration.mistudio_base_url == send.mistudio_base_url,
            MiStudioRegistration.repo_id == unit["repo_id"],
            MiStudioRegistration.split == unit["split"],
            MiStudioRegistration.commit == commit,
        )
    ).scalar_one_or_none()
    if known is not None and (unit["config"] or None) == (known.config or None):
        _write(db, step, mistudio_dataset_id=known.mistudio_dataset_id, commit=commit)
        return "reused"
    try:
        created = client.download(
            unit["repo_id"],
            unit["config"],
            unit["split"],
            revision=commit if caps.download_revision else None,
        )
    except mc.MiStudioDatasetExists as exc:
        existing = client.find_dataset(unit["repo_id"], unit["config"], unit["split"])
        existing_id = existing["id"] if existing else None
        raise StepFailed(
            "mistudio_dataset_exists",
            f"miStudio already holds {unit['repo_id']} {unit['split']} (dataset {existing_id}), "
            "possibly an older commit. It is not reused: no send recorded it at this commit.",
            "Remove it in miStudio, or send to a new repository, then resume.",
            mistudio_dataset_id=existing_id,
            detail=exc.detail,
        ) from exc
    dataset_id = str(created["id"])
    _write(db, step, mistudio_dataset_id=dataset_id, commit=commit)
    settings = get_settings()
    deadline = time.monotonic() + settings.detector_send_download_timeout_minutes * 60
    while True:
        status = client.get_dataset(dataset_id)
        state = str(status["status"]).lower()
        record_progress(job_id, message=f"miStudio download {state}: {unit['repo_id']}", force=True)
        if state == "ready":
            break
        if state == "error":
            raise StepFailed(
                "mistudio_download_failed",
                f"miStudio's download of {unit['repo_id']} ended in error: "
                f"{status.get('error_message') or 'no message'}",
                f"Check that miStudio's stored Hugging Face token can read "
                f"{unit['repo_id'].split('/')[0]}, then resume.",
                mistudio_dataset_id=dataset_id,
            )
        if time.monotonic() > deadline:
            raise StepFailed(
                "mistudio_download_timeout",
                f"miStudio's download of {unit['repo_id']} was not ready after "
                f"{settings.detector_send_download_timeout_minutes:g} minutes.",
                "Resume the send to keep waiting.",
                mistudio_dataset_id=dataset_id,
            )
        checker.raise_if_cancelled("Cancelled while miStudio downloaded.")
        SLEEP(settings.detector_send_poll_seconds)
    db.add(
        MiStudioRegistration(
            id=new_id("msr"),
            kind="dataset",
            mistudio_base_url=send.mistudio_base_url,
            repo_id=unit["repo_id"],
            config=unit["config"],
            split=unit["split"],
            commit=commit,
            mistudio_dataset_id=dataset_id,
            version_id=unit["version_id"],
            send_id=send.id,
        )
    )
    db.commit()
    return "done"


def do_register(
    db: Session,
    send: DetectorSend,
    step: DetectorSendStep,
    client: mc.MiStudioClient,
    caps: capabilities.Capabilities,
) -> str:
    role = _unit(send, "register", step.unit_key)
    download = db.execute(
        select(DetectorSendStep).where(
            DetectorSendStep.send_id == send.id,
            DetectorSendStep.step == "download",
            DetectorSendStep.unit_key == _download_key(send, role),
        )
    ).scalar_one()
    dataset_id = download.mistudio_dataset_id
    assert dataset_id is not None
    known = db.execute(
        select(MiStudioRegistration).where(
            MiStudioRegistration.kind == "view",
            MiStudioRegistration.mistudio_base_url == send.mistudio_base_url,
            MiStudioRegistration.mistudio_dataset_id == dataset_id,
            MiStudioRegistration.role == role["role"],
            MiStudioRegistration.mapping_sha256 == role["mapping_sha256"],
            MiStudioRegistration.columns_sha256 == role["columns_sha256"],
        )
    ).scalar_one_or_none()
    if known is not None:
        _write(
            db,
            step,
            probe_dataset_id=known.probe_dataset_id,
            mistudio_dataset_id=dataset_id,
            registered_counts=known.registered_counts,
        )
        return "reused"
    template = role["body"]
    manifest = None
    if caps.dataset_version_manifest:
        pub_step = _publish_step_for(db, send, role["version_id"])
        if pub_step.publish_id:
            manifest = publish_service.published_manifest_for(db, pub_step.publish_id)
    body = plan.registration_body(
        name=template["name"],
        dataset_id=dataset_id,
        config=template["config"],
        split=template["split"],
        input_column=template["input_column"],
        label_column=template["label_column"],
        label_mapping=template["label_mapping"],
        role=role["role"],
        pair_column=template.get("pair_column"),
        manifest=manifest,
        manifest_served=caps.dataset_version_manifest,
    )
    _write(db, step, request_body=body, mistudio_dataset_id=dataset_id)
    try:
        view = client.register_view(body)
    except mc.MiStudioRefused as exc:
        if exc.status == 422:
            raise StepFailed(
                "registration_refused",
                f"miStudio refused the registration: {exc.detail}",
                "The mapping check should have caught this; report it.",
                detail=exc.detail,
            ) from exc
        raise
    counts = view["counts"]
    registered = registered_counts(counts)
    expected = dict(role["expected_counts"])
    _write(db, step, registered_counts=registered, response_sha256=canonical_sha256(view))
    differ = counts_differ(registered, expected)
    if differ:
        said = ", ".join(
            f"{registered[k]:,} {k} (the version has {int(expected.get(k, 0)):,})" for k in differ
        )
        raise StepFailed(
            "counts_mismatch",
            f"miStudio registered {said} rows for split {template['split']} under this mapping. "
            "miStudio may hold a stale or different split.",
            "Remove the dataset in miStudio or send to a new repository, then resume.",
            probe_dataset_id=view["id"],
            registered=registered,
            expected=expected,
        )
    db.add(
        MiStudioRegistration(
            id=new_id("msr"),
            kind="view",
            mistudio_base_url=send.mistudio_base_url,
            split=template["split"],
            mistudio_dataset_id=dataset_id,
            role=role["role"],
            mapping_sha256=role["mapping_sha256"],
            columns_sha256=role["columns_sha256"],
            probe_dataset_id=str(view["id"]),
            version_id=role["version_id"],
            send_id=send.id,
            registered_counts=registered,
        )
    )
    _write(db, step, probe_dataset_id=str(view["id"]))
    return "done"


#: The registration counts compared with the version's: excluded too, because a null label left
#: unmapped is excluded here and unparseable in miStudio, and only the excluded count shows it.
COMPARED_COUNTS = ("positive", "negative", "excluded")


def registered_counts(counts: dict[str, Any]) -> dict[str, int]:
    """miStudio's counts for the compared keys AS REPORTED: a key it omits stays absent, never 0,
    so an older response without ``excluded`` is not read as "0 excluded"."""
    return {k: int(counts[k]) for k in COMPARED_COUNTS if k in counts}


def counts_differ(registered: dict[str, int], expected: dict[str, Any]) -> list[str]:
    """The counts miStudio reported that disagree with the version's; ``excluded`` only where
    miStudio reports it (positive and negative are always required)."""
    out = []
    for key in COMPARED_COUNTS:
        if key == "excluded" and key not in registered:
            continue
        if int(registered.get(key, -1)) != int(expected.get(key, 0)):
            out.append(key)
    return out


def _download_key(send: DetectorSend, role: dict[str, Any]) -> str:
    unit = next(u for u in send.plan["download_units"] if role["role_id"] in u["role_ids"])
    return str(unit["key"])


# --- the job ----------------------------------------------------------------------------------


def _fail(
    db: Session, job_id: str, send: DetectorSend, step: DetectorSendStep | None, err: dict[str, Any]
) -> dict[str, Any]:
    db.rollback()
    send = db.get(DetectorSend, send.id, populate_existing=True)  # type: ignore[assignment]
    if step is not None:
        step = db.get(
            DetectorSendStep, (step.send_id, step.step, step.unit_key), populate_existing=True
        )
        assert step is not None
        _write(db, step, state="failed", error=err, finished_at=utc_now())
    where = {"step": step.step, "unit": step.unit_key, "role_ids": step.role_ids} if step else {}
    send.state = "failed"
    send.error = {**where, **err}
    send.completed_at = utc_now()
    db.commit()
    logger.info(
        "detector_send.step send=%s step=%s unit=%s outcome=failed code=%s",
        send.id,
        where.get("step"),
        where.get("unit"),
        err.get("code"),
    )
    record_progress(
        job_id,
        status="failed",
        error=err.get("message", "failed")[:4000],
        result={"send_id": send.id},
    )
    emit(
        _room(send.id),
        "detector_send:status",
        {"send_id": send.id, "state": "failed", "error": send.error},
    )
    return {"status": "failed", "send_id": send.id}


@cooperative_cancel
def run_job(job_id: str) -> dict[str, Any]:
    with get_sync_db() as db:
        job = claim_job(db, job_id)
        if job is None:
            return {"status": "skipped", "job_id": job_id}
        send = db.get(DetectorSend, str(job.params["send_id"]))
        if send is None:
            # The row is committed before dispatch; a job without one never runs (FTID 009 s5).
            record_progress(job_id, status="failed", error="No send record for this job.")
            return {"status": "failed", "job_id": job_id}
        send.state = "running"
        db.commit()
        emit(_room(send.id), "detector_send:status", {"send_id": send.id, "state": "running"})
        checker = CancelCheck(job_id)
        client = CLIENT_FACTORY(send.mistudio_base_url)
        current: DetectorSendStep | None = None
        try:
            try:
                caps = capabilities.read(client, fresh=True)
            except mc.MiStudioError as exc:
                return _fail(db, job_id, send, None, {"code": exc.code, "message": exc.message})
            steps = list(
                db.execute(
                    select(DetectorSendStep)
                    .where(DetectorSendStep.send_id == send.id)
                    .order_by(DetectorSendStep.position)
                ).scalars()
            )
            total = len(steps) or 1
            for index, step in enumerate(steps):
                if step.state in ("done", "reused"):
                    continue
                # A step boundary is rare and coarse: poll the row now, not on the time throttle.
                if checker.poll_now():
                    raise OperatorCancelled(
                        job_id, checker.reason or "cancelled", f"before {step.step} {step.unit_key}"
                    )
                current = step
                _write(db, step, state="running", started_at=utc_now(), error=None)
                emit(
                    _room(send.id),
                    "detector_send:step",
                    {
                        "send_id": send.id,
                        "step": step.step,
                        "unit": step.unit_key,
                        "state": "running",
                    },
                )
                if step.step == "publish":
                    outcome = do_publish(db, send, step, job_id, checker)
                elif step.step == "download":
                    outcome = do_download(db, send, step, job_id, checker, client, caps)
                else:
                    outcome = do_register(db, send, step, client, caps)
                _write(db, step, state=outcome, finished_at=utc_now())
                logger.info(
                    "detector_send.step send=%s step=%s unit=%s outcome=%s mistudio_dataset=%s probe_dataset=%s",
                    send.id,
                    step.step,
                    step.unit_key,
                    outcome,
                    step.mistudio_dataset_id,
                    step.probe_dataset_id,
                )
                record_progress(
                    job_id, progress=100.0 * (index + 1) / total, message=f"{step.step} {outcome}"
                )
                emit(
                    _room(send.id),
                    "detector_send:step",
                    {
                        "send_id": send.id,
                        "step": step.step,
                        "unit": step.unit_key,
                        "state": outcome,
                    },
                )
                current = None
        except StepFailed as exc:
            return _fail(db, job_id, send, current, exc.as_dict())
        except mc.MiStudioError as exc:
            return _fail(
                db,
                job_id,
                send,
                current,
                {
                    "code": exc.code,
                    "message": exc.message,
                    "details": {"status": exc.status, "detail": exc.detail},
                },
            )
        except OperatorCancelled:
            db.rollback()
            if current is not None:
                row = db.get(
                    DetectorSendStep,
                    (current.send_id, current.step, current.unit_key),
                    populate_existing=True,
                )
                if row is not None and row.state == "running":
                    _write(db, row, state="pending")
            sent = db.get(DetectorSend, send.id, populate_existing=True)
            assert sent is not None
            sent.state = "cancelled"
            sent.completed_at = utc_now()
            db.commit()
            emit(_room(send.id), "detector_send:status", {"send_id": send.id, "state": "cancelled"})
            raise
        except Exception as exc:
            logger.exception("detector_send %s failed", send.id)
            return _fail(
                db,
                job_id,
                send,
                current,
                {"code": "send_failed", "message": f"{type(exc).__name__}: {exc}"[:2000]},
            )
        finally:
            client.close()
        send.state = "completed"
        send.completed_at = utc_now()
        db.commit()
        send_id = send.id
    record_progress(
        job_id,
        status="completed",
        progress=100.0,
        message="Sent to miStudio.",
        result={"send_id": send_id},
    )
    emit(_room(send_id), "detector_send:status", {"send_id": send_id, "state": "completed"})
    return {"status": "completed", "send_id": send_id}


def reap_send(db: Session, job_id: str, status: str, reason: str) -> None:
    """Janitor hook: a reaped send job fails (or cancels) its send too."""
    send = db.execute(
        select(DetectorSend).where(DetectorSend.job_id == job_id)
    ).scalar_one_or_none()
    if send is None or send.state not in ("queued", "running"):
        return
    send.state = "cancelled" if status == "cancelled" else "failed"
    send.error = {"code": "worker_lost", "message": reason}
    send.completed_at = utc_now()
    db.commit()


janitor.register_on_reap("mistudio_send", reap_send)
