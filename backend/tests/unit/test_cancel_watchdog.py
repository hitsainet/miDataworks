"""``CancelWatchdog`` (001 FTASKS 3.5, 3.6; FR-001.9).

A cancel interrupts a long pure-Python loop; nothing is raised after the block exits; ``on_tick``
runs each interval; an ``on_tick`` error is logged and does not stop the watchdog;
``OperatorCancelled`` escapes ``except Exception``. A real job row's cancel request is honoured too.
"""

from __future__ import annotations

import time

import pytest

from src.core.cancellation import CancelWatchdog, OperatorCancelled


class _Checker:
    """Says "cancelled" from its Nth poll on (stands in for the job row)."""

    def __init__(self, after: int) -> None:
        self.calls = 0
        self.after = after
        self.reason = "cancelled"

    def poll_now(self) -> bool:
        self.calls += 1
        return self.calls >= self.after


def _spin(seconds: float) -> None:
    started = time.monotonic()
    while time.monotonic() - started < seconds:
        sum(range(1000))


def test_a_cancel_interrupts_a_long_pure_python_loop() -> None:
    started = time.monotonic()
    with pytest.raises(OperatorCancelled) as info:
        with CancelWatchdog("job_1", interval_s=0.05, checker=_Checker(3)):  # type: ignore[arg-type]
            _spin(5)
    assert info.value.job_id == "job_1"
    assert time.monotonic() - started < 2


def test_on_tick_runs_each_interval_before_the_poll() -> None:
    order: list[str] = []
    checker = _Checker(4)
    real_poll = checker.poll_now

    def poll() -> bool:
        order.append("poll")
        return real_poll()

    checker.poll_now = poll  # type: ignore[method-assign]
    with pytest.raises(OperatorCancelled):
        with CancelWatchdog(
            "job_t", interval_s=0.03, checker=checker, on_tick=lambda: order.append("tick")  # type: ignore[arg-type]
        ):
            _spin(5)
    assert order[:4] == ["tick", "poll", "tick", "poll"]
    assert order.count("tick") >= 4


def test_nothing_is_raised_after_the_block_exits() -> None:
    checker = _Checker(10_000)
    with CancelWatchdog("job_1", interval_s=0.02, checker=checker):  # type: ignore[arg-type]
        pass
    checker.after = 0
    _spin(0.2)  # a late cancel must not surface here


def test_an_on_tick_error_is_logged_and_does_not_stop_the_watchdog(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken() -> None:
        raise RuntimeError("disk read failed")

    with pytest.raises(OperatorCancelled):
        with CancelWatchdog("job_2", interval_s=0.05, checker=_Checker(3), on_tick=broken):  # type: ignore[arg-type]
            _spin(5)
    assert "tick failed" in caplog.text


def test_operator_cancelled_escapes_except_exception() -> None:
    caught: list[str] = []
    started = time.monotonic()
    with pytest.raises(OperatorCancelled):
        with CancelWatchdog("job_3", interval_s=0.05, checker=_Checker(2)):  # type: ignore[arg-type]
            while time.monotonic() - started < 5:
                try:
                    sum(range(1000))
                except Exception:  # noqa: BLE001 - the point of the test
                    caught.append("swallowed")
    assert caught == []


def test_a_real_cancel_request_on_the_job_row_stops_the_block(clean_db: None) -> None:
    from sqlalchemy import update

    from src.core.clock import utc_now
    from src.core.database import get_sync_db
    from src.models.job import Job
    from tests.support import db_factories

    with get_sync_db() as db:
        job_id = db_factories.job(db, status="running").id
        db.commit()
    started = time.monotonic()
    with pytest.raises(OperatorCancelled):
        with CancelWatchdog(job_id, interval_s=0.05):
            with get_sync_db() as db:
                db.execute(
                    update(Job).where(Job.id == job_id).values(cancel_requested_at=utc_now())
                )
                db.commit()
            _spin(5)
    assert time.monotonic() - started < 3
