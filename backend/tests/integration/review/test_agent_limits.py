"""Agents accept and flag only (006 FTASKS 8.7; P-10, P-12, FR-006.42)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from src.core.database import sync_session_factory
from src.models.review import ReviewDecision
from tests.support.calibration_fixtures import make_run, make_version, row_key

AGENT = {"X-Dataworks-Agent": "agent:dataworks-mcp"}


def decision_count() -> int:
    with sync_session_factory()() as s:
        return int(s.execute(select(func.count()).select_from(ReviewDecision)).scalar_one())


@pytest.fixture
async def item_id(client: httpx.AsyncClient, operator_name: str, data_dir: Path) -> str:
    rows = [{"text": f"row {i}"} for i in range(10)]
    version = make_version(data_dir, rows)
    run = make_run(version, {row_key(r["text"]): 0.5 for r in rows})
    queue = (
        await client.post(
            "/api/v1/review-queues",
            json={"kind": "label_review", "label_run_id": run.id, "row_keys": [row_key("row 0")]},
        )
    ).json()
    page = (await client.get(f"/api/v1/review-queues/{queue['id']}/items")).json()
    return str(page["items"][0]["id"])


@pytest.mark.parametrize(
    "body",
    [
        {"decision": "override", "override_label": "humorous", "reason": "agent thinks so"},
        {"decision": "reject", "reason": "agent rejects"},
    ],
)
async def test_agent_override_and_reject_are_refused_and_write_nothing(
    client: httpx.AsyncClient, item_id: str, body: dict[str, Any]
) -> None:
    response = await client.post(
        f"/api/v1/review-items/{item_id}/decisions", json=body, headers=AGENT
    )
    if body["decision"] == "reject":
        # reject on a non-external queue is refused before the agent rule (T-29), also writing nothing
        assert response.status_code == 422
    else:
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "AGENT_DECISION_NOT_ALLOWED"
    assert decision_count() == 0


@pytest.mark.parametrize("decision", ["accept", "flag"])
async def test_agent_accept_and_flag_are_recorded_with_the_token_identity(
    client: httpx.AsyncClient, item_id: str, decision: str
) -> None:
    body: dict[str, Any] = {"decision": decision}
    if decision == "flag":
        body["reason"] = "looks off"
    response = await client.post(
        f"/api/v1/review-items/{item_id}/decisions", json=body, headers=AGENT
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["decided_by"] == "agent:dataworks-mcp" and data["decided_by_origin"] == "agent"
    assert decision_count() == 1


async def test_a_body_who_is_never_read(client: httpx.AsyncClient, item_id: str) -> None:
    response = await client.post(
        f"/api/v1/review-items/{item_id}/decisions",
        json={"decision": "accept", "decided_by": "someone else"},
        headers=AGENT,
    )
    assert response.status_code == 422  # extra field refused, never trusted
    assert decision_count() == 0
