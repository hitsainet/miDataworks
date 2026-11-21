"""Jobs REST: list, detail, cancel, dismiss, and their refusals (Foundation task 5.7)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from src.core.database import get_sync_db
from src.models.job import Job


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture Celery sends instead of reaching the broker."""
    from src.core.celery_app import celery_app

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        celery_app,
        "send_task",
        lambda name, args, task_id: calls.append({"name": name, "args": args, "task_id": task_id}),
    )
    return calls


def _set_status(job_id: str, status: str) -> None:
    with get_sync_db() as db:
        row = db.get(Job, job_id)
        assert row is not None
        row.status = status
        db.commit()


async def _start(client: httpx.AsyncClient, **body: Any) -> dict[str, Any]:
    response = await client.post("/api/v1/jobs/selftest", json={"duration_seconds": 1, **body})
    assert response.status_code == 201, response.text
    return response.json()


async def test_starting_a_job_creates_and_dispatches_it(
    client: httpx.AsyncClient, operator_name: str, sent: list[dict[str, Any]]
) -> None:
    job = await _start(client)
    assert job["status"] == "queued" and job["kind"] == "selftest"
    assert job["started_by"] == operator_name and job["started_by_origin"] == "operator"
    assert job["room"] == f"dataworks/selftest/{job['id']}"
    assert len(sent) == 1
    assert sent[0]["name"] == "midataworks.selftest.run" and sent[0]["args"] == [job["id"]]


async def test_without_an_operator_name_a_job_is_refused(
    client: httpx.AsyncClient, sent: list[dict[str, Any]]
) -> None:
    response = await client.post("/api/v1/jobs/selftest", json={})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "NO_IDENTITY"
    assert "Settings" in response.json()["error"]["message"]
    assert sent == []


async def test_an_agent_job_records_the_agent_identity(
    client: httpx.AsyncClient, sent: list[dict[str, Any]]
) -> None:
    response = await client.post(
        "/api/v1/jobs/selftest", json={}, headers={"X-Dataworks-Agent": "agent:dataworks-mcp"}
    )
    assert response.status_code == 201
    assert response.json()["started_by"] == "agent:dataworks-mcp"
    assert response.json()["started_by_origin"] == "agent"


async def test_a_malformed_agent_header_is_refused(client: httpx.AsyncClient) -> None:
    for value in ("", "dataworks-mcp", "agent:UPPER", "agent:" + "x" * 60):
        response = await client.post(
            "/api/v1/jobs/selftest", json={}, headers={"X-Dataworks-Agent": value}
        )
        assert response.status_code == 400, value
        assert response.json()["error"]["code"] == "INVALID_AGENT_IDENTITY"


async def test_list_detail_and_filters(
    client: httpx.AsyncClient, operator_name: str, sent: list[dict[str, Any]]
) -> None:
    a = await _start(client)
    b = await _start(client)
    _set_status(b["id"], "failed")
    active = (await client.get("/api/v1/jobs", params={"state": "active"})).json()["jobs"]
    failed = (await client.get("/api/v1/jobs", params={"state": "failed"})).json()["jobs"]
    assert [j["id"] for j in active] == [a["id"]]
    assert [j["id"] for j in failed] == [b["id"]]
    detail = await client.get(f"/api/v1/jobs/{a['id']}")
    assert detail.json()["id"] == a["id"]
    missing = await client.get("/api/v1/jobs/job_nope")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "JOB_NOT_FOUND"


async def test_cancelling_a_queued_job_cancels_it_now(
    client: httpx.AsyncClient, operator_name: str, sent: list[dict[str, Any]]
) -> None:
    job = await _start(client)
    response = await client.post(f"/api/v1/jobs/{job['id']}/cancel", json={"reason": "not needed"})
    assert response.status_code == 202
    body = response.json()
    assert body["job"]["status"] == "cancelled" and "will not run" in body["detail"]


async def test_cancelling_a_running_job_asks_it_to_stop(
    client: httpx.AsyncClient, operator_name: str, sent: list[dict[str, Any]]
) -> None:
    job = await _start(client)
    _set_status(job["id"], "running")
    body = (await client.post(f"/api/v1/jobs/{job['id']}/cancel")).json()
    assert body["job"]["status"] == "cancelling"
    assert body["job"]["cancel_requested_at"] is not None
    again = await client.post(f"/api/v1/jobs/{job['id']}/cancel")
    assert again.status_code == 202 and "already requested" in again.json()["detail"]


@pytest.mark.parametrize("terminal", ["cancelled", "completed", "failed"])
async def test_cancel_on_a_terminal_job_is_409(
    client: httpx.AsyncClient, operator_name: str, sent: list[dict[str, Any]], terminal: str
) -> None:
    job = await _start(client)
    _set_status(job["id"], terminal)
    response = await client.post(f"/api/v1/jobs/{job['id']}/cancel")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "JOB_TERMINAL"
    assert response.json()["error"]["details"]["status"] == terminal


async def test_cancel_on_a_missing_job_is_404(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/jobs/job_missing/cancel")
    assert response.status_code == 404


async def test_dismissing_a_failed_job_hides_it_and_keeps_it(
    client: httpx.AsyncClient, operator_name: str, sent: list[dict[str, Any]]
) -> None:
    job = await _start(client)
    _set_status(job["id"], "failed")
    response = await client.post(f"/api/v1/jobs/{job['id']}/dismiss")
    assert response.status_code == 200 and response.json()["dismissed_at"] is not None
    failed = (await client.get("/api/v1/jobs", params={"state": "failed"})).json()["jobs"]
    assert failed == []
    assert (await client.get(f"/api/v1/jobs/{job['id']}")).status_code == 200


async def test_dismissing_a_job_that_did_not_fail_is_409(
    client: httpx.AsyncClient, operator_name: str, sent: list[dict[str, Any]]
) -> None:
    job = await _start(client)
    response = await client.post(f"/api/v1/jobs/{job['id']}/dismiss")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "JOB_NOT_FAILED"
