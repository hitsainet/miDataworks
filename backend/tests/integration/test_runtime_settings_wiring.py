"""The refresh points and the real readers (feature 012; ENH-003 §4 "Wiring" and "Refresh points").

Each test changes a setting the way production does (the route, or a stored row another process
wrote) and then runs the REAL reader: a route handler, a real Celery task, the janitor's sweep, a
label run's chunk loop, the root logger. Each refresh point has a negative control recorded in
``0xcc/reviews/012_013_implementation_controls_2026-10-20.md``: delete the line, this file goes red.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from src.core import runtime_settings as rs
from src.core.clock import utc_now
from src.core.config import env_settings, get_settings
from src.core.database import get_sync_engine, sync_session_factory
from src.models.job import Job
from tests.support import db_factories
from tests.support.labeling_fixtures import labeling

__all__ = ["labeling"]


def _store(key: str, value: str) -> None:
    """A row as ANOTHER process's write leaves it: in the table, not in this process's overlay."""
    with get_sync_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO dw_app_settings (key, value, is_sensitive, category) "
                "VALUES (:k, :v, false, 'environment') "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ),
            {"k": key, "v": value},
        )


# --- refresh after a write (the API, "next request") -------------------------------------------


async def test_a_write_is_in_force_for_the_next_request(client: httpx.AsyncClient) -> None:
    """Control: remove ``refresh_async`` from ``put_setting`` -> the meta route still shows the
    environment's cap."""
    assert (
        await client.put("/api/v1/settings/upload_max_bytes", json={"value": "4096"})
    ).status_code == 200
    meta = (await client.get("/api/v1/sources/meta")).json()
    assert meta["limits"]["upload_max_bytes"] == 4096
    assert (await client.delete("/api/v1/settings/upload_max_bytes")).status_code == 204
    meta = (await client.get("/api/v1/sources/meta")).json()
    assert meta["limits"]["upload_max_bytes"] == env_settings().upload_max_bytes


async def test_an_upload_over_the_new_cap_is_refused(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    import json

    await client.put("/api/v1/settings/upload_max_bytes", json={"value": "16"})
    response = await client.post(
        "/api/v1/sources/uploads",
        files={"files": ("rows.jsonl", b'{"text": "a row longer than sixteen bytes"}\n')},
        data={"manifest": json.dumps({"files": [{"name": "rows.jsonl", "split": "train"}]})},
    )
    assert response.status_code == 413, response.text
    assert response.json()["error"]["details"]["limit_bytes"] == 16


# --- API startup ------------------------------------------------------------------------------


async def test_the_api_start_reads_the_stored_overrides(clean_db: None) -> None:
    """Control: remove ``refresh_from_database()`` from ``main.lifespan`` -> red."""
    from src.main import fastapi_app

    _store("upload_max_bytes", "777")
    rs.reset()
    assert get_settings().upload_max_bytes == env_settings().upload_max_bytes
    async with fastapi_app.router.lifespan_context(fastapi_app):
        assert get_settings().upload_max_bytes == 777


# --- Celery task_prerun ("next task") ---------------------------------------------------------


def _agent_request(days_old: int) -> None:
    with get_sync_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO dw_agent_requests (id, identity, method, route, status_code, created_at) "
                "VALUES (:id, 'agent:x', 'GET', '/api/v1/x', 200, now() - make_interval(days => :d))"
            ),
            {"id": str(uuid.uuid4()), "d": days_old},
        )


def test_every_task_starts_with_the_stored_overrides(clean_db: None) -> None:
    """A REAL task (the daily prune) run through Celery's tracer. Control: remove the
    ``task_prerun.connect`` line in ``core/celery_app.py`` -> the prune keeps the 30-day rule."""
    from src.workers.approval_tasks import prune_agent_requests_task

    _agent_request(5)
    _agent_request(40)
    _store("agent_request_retention_days", "2")
    rs.reset()
    assert get_settings().agent_request_retention_days == 30
    result = prune_agent_requests_task.apply()
    assert result.get() == {"pruned": 2}
    assert get_settings().agent_request_retention_days == 2


# --- the janitor ("next janitor pass") --------------------------------------------------------


async def test_the_janitor_judges_by_the_limit_in_force(client: httpx.AsyncClient) -> None:
    """012 FTID I3: the limit was captured at import. Control: the janitor reading
    ``kind.janitor_heartbeat_limit`` again -> the job survives."""
    from src.workers import janitor

    with sync_session_factory()() as db:
        job = db_factories.job(db, kind="publish_reverify", status="running")
        job.started_at = job.heartbeat_at = utc_now() - timedelta(seconds=300)
        db.commit()
        job_id = job.id
        assert janitor.sweep(db, active_ids=set()) == []  # 1800 s by default
    response = await client.put(
        "/api/v1/settings/publish_janitor_limit_seconds", json={"value": "200"}
    )
    assert response.status_code == 200, response.text
    with sync_session_factory()() as db:
        reaped = janitor.sweep(db, active_ids=set())
        assert [r.job_id for r in reaped] == [job_id]
        assert "limit 200 s" in reaped[0].reason
        assert db.get(Job, job_id).status == "failed"  # type: ignore[union-attr]


# --- the log level ("now" in the API, "next task" in a worker) -------------------------------


async def test_the_log_level_changes_now_in_the_api(client: httpx.AsyncClient) -> None:
    root = logging.getLogger()
    before = root.level
    try:
        await client.put("/api/v1/settings/log_level", json={"value": "warning"})
        assert root.level == logging.WARNING
        await client.delete("/api/v1/settings/log_level")
        assert logging.getLevelName(root.level) == env_settings().log_level
    finally:
        root.setLevel(before)


def test_the_log_level_changes_in_a_worker_at_its_next_task(clean_db: None) -> None:
    from src.workers.approval_tasks import prune_agent_requests_task

    root = logging.getLogger()
    before = root.level
    try:
        _store("log_level", "ERROR")
        rs.reset()
        prune_agent_requests_task.apply()
        assert root.level == logging.ERROR
    finally:
        rs.reset()
        root.setLevel(before)


# --- long runs ("next check in a running run") ------------------------------------------------


async def test_a_running_label_run_picks_up_a_change_at_its_next_chunk(
    client: httpx.AsyncClient, labeling: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """012 FTID I6. The run starts under the environment's cap (20 failures in a row). While
    the FIRST chunk runs, another process stores a cap of 3; failures start in the second chunk.
    The run must stop after 3, not 20. Control: remove the refresh in ``_boundary`` -> 20."""
    from tests.integration.labeling.helpers import setup_classifier, start_body

    monkeypatch.setattr(rs, "BOUNDARY_MAX_AGE_S", 0.0)
    lab = labeling
    version_id, template_id = await setup_classifier(client, lab, n=450)  # type: ignore[arg-type]
    run = (await client.post("/api/v1/label-runs", json=start_body(version_id, template_id))).json()

    def on_score(n: int) -> None:
        if n == 150:
            _store("label_max_consecutive_failures", "3")
        if n == 250:
            lab.millm.fail_status = 422  # type: ignore[attr-defined]

    lab.millm.on_score = on_score  # type: ignore[attr-defined]
    final = lab.run_until_done(run["id"])  # type: ignore[attr-defined]
    assert final.state == "failed"
    assert final.error is not None and final.error["code"] == "ENDPOINT_FAILING"
    assert final.error["message"].startswith("3 consecutive rows failed"), final.error["message"]


async def test_a_batch_run_reads_its_poll_interval_at_each_poll(
    client: httpx.AsyncClient, labeling: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """012 FTID I6, "next poll". The batch takes three polls; after the first wait another process
    stores a new interval; the next wait uses it. Control: read the interval once before the poll
    loop again -> every wait is the environment's 5 s."""
    from tests.integration.labeling.helpers import setup_classifier, start_body

    monkeypatch.setattr(rs, "BOUNDARY_MAX_AGE_S", 0.0)
    lab: Any = labeling
    lab.millm.batch_polls_to_complete = 3

    class Waits(list[float]):
        def append(self, seconds: float) -> None:
            if not self:
                _store("label_batch_poll_seconds", "1.5")
            super().append(seconds)

    lab.waits = Waits()
    version_id, template_id = await setup_classifier(client, lab, n=20)
    body = start_body(version_id, template_id, transport="batch")
    run = (await client.post("/api/v1/label-runs", json=body)).json()
    final = lab.run_until_done(run["id"])
    assert final.state == "completed", final.error
    assert list(lab.waits)[:2] == [env_settings().label_batch_poll_seconds, 1.5]
