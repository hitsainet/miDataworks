"""``record_progress``: time throttle, heartbeat, terminal guard (Foundation tasks 5.3, 5.4).

Mutation controls (5.9): remove the terminal guard; replace the time throttle with a count.
Each must turn this file red.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from src.core import clock
from src.core.cancellation import CancelCheck, OperatorCancelled, record_progress
from src.core.database import get_sync_db, get_sync_engine
from src.models.job import Job


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def fake_clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(clock, "monotonic", fake)
    return fake


def _insert(status: str = "running", **extra: object) -> str:
    job_id = f"job_{status}_{abs(hash((status, tuple(extra)))) % 10**8}"
    with get_sync_db() as db:
        db.add(
            Job(
                id=job_id,
                kind="selftest",
                status=status,
                progress=0.0,
                params={},
                started_by="Tester",
                started_by_origin="operator",
                **extra,
            )
        )
        db.commit()
    return job_id


def _row(job_id: str) -> Job:
    with get_sync_db() as db:
        row = db.get(Job, job_id)
        assert row is not None
        db.expunge(row)
        return row


def _heartbeat_writes(job_id: str) -> object:
    with get_sync_engine().connect() as conn:
        return conn.execute(
            text("SELECT heartbeat_at FROM dw_jobs WHERE id = :i"), {"i": job_id}
        ).scalar()


def test_the_first_call_writes_and_stamps_the_heartbeat(
    clean_db: None, fake_clock: FakeClock
) -> None:
    job_id = _insert()
    assert record_progress(job_id, progress=10.0) is True
    row = _row(job_id)
    assert row.progress == 10.0 and row.heartbeat_at is not None


def test_progress_is_throttled_by_elapsed_time_not_call_count(
    clean_db: None, fake_clock: FakeClock
) -> None:
    job_id = _insert()
    assert record_progress(job_id, progress=1.0) is True
    # Thirty calls inside the interval: none writes. A count throttle (every 25th) would.
    written = [record_progress(job_id, progress=float(i)) for i in range(2, 32)]
    assert written == [False] * 30
    assert _row(job_id).progress == 1.0
    fake_clock.now += 59.0
    assert record_progress(job_id, progress=40.0) is False
    # One call after the interval writes, though it is only the 33rd call.
    fake_clock.now += 2.0
    assert record_progress(job_id, progress=50.0) is True
    assert _row(job_id).progress == 50.0


def test_two_calls_far_apart_both_write(clean_db: None, fake_clock: FakeClock) -> None:
    """A count throttle (every Nth call) would refuse the second call."""
    job_id = _insert()
    assert record_progress(job_id, progress=1.0) is True
    first = _heartbeat_writes(job_id)
    fake_clock.now += 61.0
    assert record_progress(job_id, progress=2.0) is True
    assert _heartbeat_writes(job_id) != first


def test_status_changes_always_write(clean_db: None, fake_clock: FakeClock) -> None:
    job_id = _insert()
    assert record_progress(job_id, progress=1.0) is True
    assert record_progress(job_id, status="completed", progress=100.0) is True
    row = _row(job_id)
    assert row.status == "completed" and row.completed_at is not None


def test_a_cancelled_row_stays_cancelled_after_a_late_progress_write(
    clean_db: None, fake_clock: FakeClock
) -> None:
    job_id = _insert(status="cancelled")
    fake_clock.now += 120
    assert record_progress(job_id, progress=80.0, force=True) is False
    assert record_progress(job_id, status="running") is False
    assert record_progress(job_id, status="completed", result={"x": 1}) is False
    row = _row(job_id)
    assert row.status == "cancelled" and row.progress == 0.0 and row.result is None


def test_a_cancelling_row_cannot_step_back_to_running(
    clean_db: None, fake_clock: FakeClock
) -> None:
    job_id = _insert(status="cancelling")
    assert record_progress(job_id, status="running") is False
    assert _row(job_id).status == "cancelling"
    assert record_progress(job_id, progress=30.0, force=True) is True  # still beating
    assert record_progress(job_id, status="cancelled") is True
    assert _row(job_id).status == "cancelled"


def test_the_checker_sees_a_cancel_request(clean_db: None, fake_clock: FakeClock) -> None:
    job_id = _insert()
    checker = CancelCheck(job_id, min_interval_s=2.0)
    assert checker() is False  # first call polls
    with get_sync_engine().begin() as conn:
        conn.execute(
            text("UPDATE dw_jobs SET status='cancelling', cancel_requested_at=now() WHERE id=:i"),
            {"i": job_id},
        )
    assert checker() is False  # throttled: within 2 s
    fake_clock.now += 2.5
    with pytest.raises(OperatorCancelled):
        checker.raise_if_cancelled()


def test_the_checker_treats_a_deleted_row_as_a_stop(clean_db: None, fake_clock: FakeClock) -> None:
    job_id = _insert()
    with get_sync_engine().begin() as conn:
        conn.execute(text("DELETE FROM dw_jobs WHERE id=:i"), {"i": job_id})
    checker = CancelCheck(job_id)
    assert checker() is True and checker.reason == "deleted"


def test_heartbeat_interval_comes_from_settings(
    clean_db: None, fake_clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.core.config import get_settings

    monkeypatch.setattr(get_settings(), "progress_heartbeat_seconds", 5.0)
    job_id = _insert()
    assert record_progress(job_id, progress=1.0)
    fake_clock.now += 4.0
    assert not record_progress(job_id, progress=2.0)
    fake_clock.now += 1.5
    assert record_progress(job_id, progress=3.0)
