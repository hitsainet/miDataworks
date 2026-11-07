"""The janitor reaps only dead jobs (ADR-007; Foundation task 5.5)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from src.core.clock import utc_now
from src.core.database import get_sync_db
from src.models.job import Job
from src.workers import janitor


def _job(
    status: str,
    heartbeat_age: timedelta | None,
    task_id: str | None = "task-1",
    kind: str = "selftest",
) -> str:
    now = utc_now()
    job_id = f"job_{status}_{task_id}_{kind}"
    with get_sync_db() as db:
        db.add(
            Job(
                id=job_id,
                kind=kind,
                status=status,
                progress=5.0,
                params={},
                started_by="Tester",
                started_by_origin="operator",
                celery_task_id=task_id,
                started_at=now - timedelta(hours=2),
                heartbeat_at=None if heartbeat_age is None else now - heartbeat_age,
            )
        )
        db.commit()
    return job_id


def _status(job_id: str) -> tuple[str, str | None]:
    with get_sync_db() as db:
        row = db.get(Job, job_id)
        assert row is not None
        return row.status, row.error


def test_a_live_job_with_a_recent_heartbeat_is_never_reaped(clean_db: None) -> None:
    job_id = _job("running", timedelta(seconds=90))
    with get_sync_db() as db:
        assert janitor.sweep(db, active_ids=set()) == []
    assert _status(job_id)[0] == "running"


def test_a_job_whose_heartbeat_stopped_is_reaped_with_a_reason(clean_db: None) -> None:
    job_id = _job("running", timedelta(minutes=30))
    with get_sync_db() as db:
        reaped = janitor.sweep(db, active_ids=set())
    assert [r.job_id for r in reaped] == [job_id]
    status, error = _status(job_id)
    assert status == "failed"
    assert error is not None and "stopped reporting" in error and "not retried" in error


def test_a_stale_job_celery_still_runs_is_left_alone(clean_db: None) -> None:
    job_id = _job("running", timedelta(minutes=30), task_id="task-live")
    with get_sync_db() as db:
        assert janitor.sweep(db, active_ids={"task-live"}) == []
    assert _status(job_id)[0] == "running"


def test_a_stale_cancelling_job_becomes_cancelled(clean_db: None) -> None:
    job_id = _job("cancelling", timedelta(minutes=30))
    with get_sync_db() as db:
        janitor.sweep(db, active_ids=set())
    assert _status(job_id)[0] == "cancelled"


def test_queued_and_terminal_jobs_are_never_judged(clean_db: None) -> None:
    queued = _job("queued", None, task_id="q")
    done = _job("completed", timedelta(days=3), task_id="d")
    with get_sync_db() as db:
        assert janitor.sweep(db, active_ids=set()) == []
    assert _status(queued)[0] == "queued" and _status(done)[0] == "completed"


def test_the_limit_is_per_kind(clean_db: None) -> None:
    """label_run allows 15 minutes; selftest 5. Ten minutes reaps one and spares the other."""
    selftest = _job("running", timedelta(minutes=10), task_id="a", kind="selftest")
    label = _job("running", timedelta(minutes=10), task_id="b", kind="label_run")
    with get_sync_db() as db:
        janitor.sweep(db, active_ids=set())
    assert _status(selftest)[0] == "failed"
    assert _status(label)[0] == "running"


def test_unknown_celery_state_reaps_nothing(
    clean_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = _job("running", timedelta(hours=1))
    monkeypatch.setattr(janitor, "active_task_ids", lambda: None)
    with get_sync_db() as db:
        assert janitor.sweep(db) == []
    assert _status(job_id)[0] == "running"


def test_a_job_that_never_beat_is_judged_from_its_start(clean_db: None) -> None:
    job_id = _job("running", None)
    with get_sync_db() as db:
        janitor.sweep(db, active_ids=set())
    assert _status(job_id)[0] == "failed"
