"""Cooperative cancellation rules (ADR-007; Foundation task 5.3)."""

from __future__ import annotations

import pytest

from src.core import cancellation
from src.core.cancellation import (
    OperatorCancelled,
    cooperative_cancel,
    guard_allows,
    plan_cancel,
    row_requests_cancel,
)


def test_operator_cancelled_is_not_an_exception() -> None:
    assert issubclass(OperatorCancelled, BaseException)
    assert not issubclass(OperatorCancelled, Exception)


def test_a_broad_except_exception_does_not_swallow_it() -> None:
    def task_code() -> str:
        try:
            raise OperatorCancelled("job_1")
        except Exception:  # noqa: BLE001 - the point of the test
            return "swallowed"

    with pytest.raises(OperatorCancelled):
        task_code()


def test_cooperative_cancel_returns_and_records(monkeypatch: pytest.MonkeyPatch) -> None:
    writes: list[dict[str, object]] = []
    monkeypatch.setattr(
        cancellation, "record_progress", lambda job_id, **kw: writes.append({"job": job_id, **kw})
    )

    @cooperative_cancel
    def task(job_id: str) -> str:
        try:
            raise OperatorCancelled(job_id, detail="stopped at chunk 3", result={"chunks": 3})
        except Exception:  # noqa: BLE001
            return "swallowed"

    assert task("job_9") == {"status": "cancelled", "job_id": "job_9", "reason": "cancelled"}
    assert writes == [
        {
            "job": "job_9",
            "status": "cancelled",
            "message": "stopped at chunk 3",
            "result": {"chunks": 3},
        }
    ]


@pytest.mark.parametrize(
    ("current", "incoming", "progress", "allowed"),
    [
        ("queued", "running", False, True),
        ("running", None, True, True),
        ("running", "completed", True, True),
        ("cancelling", None, True, True),
        ("cancelling", "cancelled", False, True),
        ("cancelling", "running", False, False),
        ("cancelling", "queued", False, False),
        ("cancelled", "running", False, False),
        ("cancelled", "completed", False, False),
        ("cancelled", None, True, False),
        ("cancelled", None, False, True),  # a message only: where it stopped
        ("completed", "failed", False, False),
        ("failed", None, True, False),
    ],
)
def test_the_terminal_guard(
    current: str, incoming: str | None, progress: bool, allowed: bool
) -> None:
    assert guard_allows(current, incoming, writes_progress=progress) is allowed


def test_plan_cancel() -> None:
    assert plan_cancel("queued").new_status == "cancelled"
    assert plan_cancel("running").new_status == "cancelling"
    assert plan_cancel("cancelling").allowed and plan_cancel("cancelling").new_status is None
    for terminal in ("cancelled", "completed", "failed"):
        assert not plan_cancel(terminal).allowed


def test_which_rows_tell_their_task_to_stop() -> None:
    assert row_requests_cancel("running", object())
    assert row_requests_cancel("cancelling", None)
    assert row_requests_cancel("cancelled", None)
    assert not row_requests_cancel("running", None)
