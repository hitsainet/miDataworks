"""The walking-skeleton job on a real worker (Foundation tasks 5.8, 6.4).

A real uvicorn API and a real Celery worker run as subprocesses. The job completes; cancels
mid-run and keeps what it wrote; survives a janitor pass while it sleeps longer than the
heartbeat throttle; and its progress arrives on its own Socket.IO room and on no other.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
import socketio
from sqlalchemy import text

from src.core.database import get_sync_db, get_sync_engine
from src.workers import janitor
from tests.support.live_stack import Stack, live_stack, wait_for_job


@pytest.fixture(scope="module")
def stack(migrated_database: None, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Stack]:
    with live_stack(tmp_path_factory.mktemp("selftest-stack")) as running:
        yield running


@pytest.fixture
def named(clean_db: None) -> None:
    with get_sync_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO dw_app_settings (key, value, is_sensitive, category) "
                "VALUES ('operator_name', 'Live Tester', false, 'identity')"
            )
        )


def _start(stack: Stack, **params: object) -> dict[str, object]:
    with stack.http() as http:
        response = http.post("/api/v1/jobs/selftest", json=params)
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def test_the_selftest_job_completes(stack: Stack, named: None) -> None:
    job = _start(stack, duration_seconds=1.5, step_seconds=0.25, rows=500)
    done = wait_for_job(stack, str(job["id"]), {"completed", "failed"})
    assert done["status"] == "completed", done
    assert done["progress"] == 100.0 and done["heartbeat_at"] is not None
    result = done["result"]
    assert isinstance(result, dict) and result["parquet"]["rows"] == 500
    written = stack.data_dir / "runs" / str(job["id"]) / "selftest.parquet"
    assert written.exists() and Path(result["parquet"]["path"]) == written.resolve()
    assert list((stack.data_dir / "staging").iterdir()) == []


def test_cancel_mid_run_stops_and_keeps_what_it_wrote(stack: Stack, named: None) -> None:
    job = _start(stack, duration_seconds=60, step_seconds=0.2, rows=300)
    job_id = str(job["id"])
    wait_for_job(stack, job_id, {"running"})
    time.sleep(0.6)
    with stack.http() as http:
        response = http.post(f"/api/v1/jobs/{job_id}/cancel", json={"reason": "test cancel"})
    assert response.status_code == 202 and response.json()["job"]["status"] == "cancelling"
    started = time.monotonic()
    done = wait_for_job(stack, job_id, {"cancelled", "completed", "failed"}, timeout=20)
    assert done["status"] == "cancelled", done
    assert time.monotonic() - started < 10, "the job did not stop at its next checkpoint"
    assert (stack.data_dir / "runs" / job_id / "selftest.parquet").exists()
    assert done["result"]["parquet"]["rows"] == 300  # type: ignore[index]


def test_a_sleeping_live_job_survives_a_janitor_pass(stack: Stack, named: None) -> None:
    """Each sleep (2.5 s) is longer than the heartbeat throttle (1 s); the janitor runs mid-job
    with a 5 s limit and Celery reported as running nothing, so only the heartbeat protects it."""
    job = _start(stack, duration_seconds=9, step_seconds=2.5, rows=10)
    job_id = str(job["id"])
    wait_for_job(stack, job_id, {"running"})
    for _ in range(3):
        time.sleep(2.6)
        with get_sync_db() as db:
            assert (
                janitor.sweep(db, active_ids=set(), limits={"selftest": timedelta(seconds=5)}) == []
            )
            assert janitor.sweep(db) == []  # and with the real Celery inspect
    done = wait_for_job(stack, job_id, {"completed", "failed", "cancelled"})
    assert done["status"] == "completed", done


def test_progress_arrives_on_its_room_and_no_other(stack: Stack, named: None) -> None:
    async def scenario() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        mine: list[dict[str, object]] = []
        other: list[dict[str, object]] = []
        a, b = socketio.AsyncClient(), socketio.AsyncClient()
        a.on("job:progress", lambda data: mine.append(data))
        b.on("job:progress", lambda data: other.append(data))
        for client in (a, b):
            await client.connect(
                stack.api_url, socketio_path="/api/ws/socket.io", transports=["websocket"]
            )
        job = await asyncio.to_thread(_start, stack, duration_seconds=3, step_seconds=0.25, rows=10)
        room = str(job["room"])
        assert (await a.call("subscribe", {"room": room}))["ok"] is True
        assert (await b.call("subscribe", {"room": "dataworks/selftest/job_someone_else"}))[
            "ok"
        ] is True
        await asyncio.to_thread(wait_for_job, stack, str(job["id"]), {"completed", "failed"})
        await asyncio.sleep(0.5)
        for client in (a, b):
            await client.disconnect()
        return mine, other

    mine, other = asyncio.run(scenario())
    assert len(mine) >= 3, f"too few progress events arrived: {mine}"
    assert all(str(event["room"]).startswith("dataworks/selftest/job_") for event in mine)
    assert other == []
