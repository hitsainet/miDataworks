"""Lease-aware model queueing: miLLM serves one model at a time (R-03.64, X-08; task 7.1)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from src.core.database import get_sync_db
from src.models.job import Job
from src.services.job_service import claim_job, dispatch_queued, plan_dispatch


@dataclass
class J:
    id: str
    required_model_id: str | None


class TestThePureRule:
    def test_different_models_run_one_after_the_other(self) -> None:
        decision = plan_dispatch([], [J("a", "model-1"), J("b", "model-2")])
        assert decision.dispatch == ["a"]
        assert "miLLM serves one model at a time" in decision.waiting["b"]
        assert "model-1" in decision.waiting["b"] and "model-2" in decision.waiting["b"]

    def test_the_same_model_is_allowed_together(self) -> None:
        decision = plan_dispatch([J("run", "model-1")], [J("a", "model-1"), J("b", "model-1")])
        assert decision.dispatch == ["a", "b"] and decision.waiting == {}

    def test_jobs_without_a_model_are_never_held(self) -> None:
        decision = plan_dispatch(
            [J("run", "model-1")], [J("a", None), J("b", "model-2"), J("c", None)]
        )
        assert decision.dispatch == ["a", "c"] and list(decision.waiting) == ["b"]

    def test_a_later_job_never_overtakes_an_earlier_blocked_one(self) -> None:
        decision = plan_dispatch([J("run", "model-1")], [J("b", "model-2"), J("c", "model-1")])
        assert decision.dispatch == []
        assert "Waiting behind job b" in decision.waiting["c"]


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    from src.core.celery_app import celery_app

    calls: list[str] = []
    monkeypatch.setattr(celery_app, "send_task", lambda name, args, task_id: calls.append(args[0]))
    return calls


async def _start(client: httpx.AsyncClient, model: str | None) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/jobs/selftest", json={"duration_seconds": 1, "required_model_id": model}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_two_jobs_needing_different_models_run_one_after_the_other(
    client: httpx.AsyncClient, operator_name: str, sent: list[str]
) -> None:
    first = await _start(client, "model-1")
    second = await _start(client, "model-2")
    assert sent == [first["id"]]
    assert second["status"] == "queued" and "one model at a time" in second["queue_reason"]

    # The first starts and finishes; the second is then dispatched.
    with get_sync_db() as db:
        claim_job(db, first["id"])
        assert dispatch_queued(db) == []  # still blocked while the first runs
        row = db.get(Job, first["id"])
        assert row is not None
        row.status = "completed"
        db.commit()
        assert dispatch_queued(db) == [second["id"]]
    assert sent == [first["id"], second["id"]]
    refreshed = (await client.get(f"/api/v1/jobs/{second['id']}")).json()
    assert refreshed["queue_reason"] is None


async def test_two_jobs_needing_the_same_model_are_allowed_by_the_queue(
    client: httpx.AsyncClient, operator_name: str, sent: list[str]
) -> None:
    first = await _start(client, "model-1")
    second = await _start(client, "model-1")
    assert sent == [first["id"], second["id"]]


async def test_a_cancelled_queued_job_is_not_claimed(
    client: httpx.AsyncClient, operator_name: str, sent: list[str]
) -> None:
    job = await _start(client, None)
    await client.post(f"/api/v1/jobs/{job['id']}/cancel")
    with get_sync_db() as db:
        assert claim_job(db, job["id"]) is None
