"""The queue close transition (suite tracker 4.1, operator decision D2, 2026-10-16).

``POST /api/v1/review-queues/{id}/close`` moves a queue open -> closed. miForge (F10 8.6) codes
against this to retire the queue its live measurement creates, so the shapes asserted here are the
contract written in ``docs/mcp-contract.md`` ("Review queues for applications").
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from src.api.v1.endpoints import review as review_routes
from tests.support.calibration_fixtures import make_run, make_version, row_key

MIFORGE = {"X-Dataworks-Agent": "agent:miforge"}
OTHER = {"X-Dataworks-Agent": "agent:other-app"}
MCP = {"X-Dataworks-Agent": "agent:dataworks-mcp"}


@pytest.fixture
def emits(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, dict[str, Any]]]:
    seen: list[tuple[str, str, dict[str, Any]]] = []

    async def emit(room: str, event: str, payload: dict[str, Any]) -> None:
        seen.append((room, event, payload))

    monkeypatch.setattr(review_routes, "emit_to_room", emit)
    return seen


async def external_queue(client: httpx.AsyncClient) -> str:
    r = await client.post(
        "/api/v1/review-queues",
        json={"kind": "external", "question": "Admit this sample?", "label_set": ["admit", "drop"]},
        headers=MIFORGE,
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def candidates(
    client: httpx.AsyncClient, queue_id: str, *ids: str, headers: Any = MIFORGE
) -> httpx.Response:
    body = {"candidates": [{"external_id": i, "prompt": "p", "completion": "c"} for i in ids]}
    return await client.post(
        f"/api/v1/review-queues/{queue_id}/candidates", json=body, headers=headers
    )


async def close(
    client: httpx.AsyncClient, queue_id: str, body: Any = None, headers: Any = None
) -> httpx.Response:
    return await client.post(
        f"/api/v1/review-queues/{queue_id}/close",
        json=body if body is not None else {},
        headers=headers or {},
    )


@pytest.fixture
def label_review_queue(data_dir: Path) -> tuple[str, str]:
    rows = [{"text": f"row {i}"} for i in range(120)]
    version = make_version(data_dir, rows)
    run = make_run(version, {row_key(r["text"]): (i % 10) / 10 + 0.05 for i, r in enumerate(rows)})
    return version, run.id


async def test_the_operator_closes_an_open_queue_and_the_close_is_recorded(
    client: httpx.AsyncClient, operator_name: str, emits: list[Any]
) -> None:
    queue_id = await external_queue(client)
    assert (await candidates(client, queue_id, "c1")).status_code == 201
    r = await close(client, queue_id, {"reason": "measurement done"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["id"] == queue_id and out["state"] == "closed"
    assert out["closed_by"] == operator_name and out["closed_by_origin"] == "operator"
    assert out["close_reason"] == "measurement done" and out["closed_at"]
    assert out["items"] == 1
    again = (await client.get(f"/api/v1/review-queues/{queue_id}")).json()
    assert {k: again[k] for k in ("state", "closed_at", "closed_by")} == {
        k: out[k] for k in ("state", "closed_at", "closed_by")
    }
    assert [(room, event) for room, event, _ in emits] == [
        (f"dataworks/review-queues/{queue_id}", "review:queue_closed")
    ]
    assert emits[0][2]["state"] == "closed"


async def test_closing_a_closed_queue_is_idempotent_and_keeps_the_first_close(
    client: httpx.AsyncClient, operator_name: str, emits: list[Any]
) -> None:
    queue_id = await external_queue(client)
    first = (await close(client, queue_id, {"reason": "first"}, headers=MIFORGE)).json()
    second = await close(client, queue_id, {"reason": "second"})
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["state"] == "closed"
    assert body["closed_at"] == first["closed_at"]
    assert body["closed_by"] == "agent:miforge" and body["closed_by_origin"] == "agent"
    assert body["close_reason"] == "first"
    assert len(emits) == 1  # only the change is broadcast


async def test_an_application_closes_its_own_queue_with_no_body(
    client: httpx.AsyncClient,
) -> None:
    queue_id = await external_queue(client)
    r = await client.post(f"/api/v1/review-queues/{queue_id}/close", headers=MIFORGE)
    assert r.status_code == 200, r.text
    assert r.json()["closed_by"] == "agent:miforge" and r.json()["close_reason"] is None


async def test_an_agent_cannot_close_a_queue_it_did_not_create(
    client: httpx.AsyncClient, operator_name: str, label_review_queue: tuple[str, str]
) -> None:
    queue_id = await external_queue(client)
    r = await close(client, queue_id, headers=OTHER)
    assert r.status_code == 403 and r.json()["error"]["code"] == "QUEUE_NOT_YOURS"
    _, run_id = label_review_queue
    mine = await client.post(
        "/api/v1/review-queues", json={"kind": "label_review", "label_run_id": run_id, "size": 5}
    )
    assert mine.status_code == 201, mine.text
    r = await close(client, mine.json()["id"], headers=MCP)
    assert r.status_code == 403 and r.json()["error"]["code"] == "QUEUE_NOT_YOURS"
    for qid in (queue_id, mine.json()["id"]):
        assert (await client.get(f"/api/v1/review-queues/{qid}")).json()["state"] == "open"


async def test_a_closed_queue_takes_no_decisions_and_no_candidates_but_reads_back(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    queue_id = await external_queue(client)
    await candidates(client, queue_id, "c1")
    items = (await client.get(f"/api/v1/review-queues/{queue_id}/items")).json()["items"]
    decided = await client.post(
        f"/api/v1/review-items/{items[0]['id']}/decisions", json={"decision": "accept"}
    )
    assert decided.status_code == 201, decided.text
    assert (await close(client, queue_id, headers=MIFORGE)).status_code == 200
    refused = await client.post(
        f"/api/v1/review-items/{items[0]['id']}/decisions", json={"decision": "accept"}
    )
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "QUEUE_CLOSED"
    late = await candidates(client, queue_id, "c2")
    assert late.status_code == 409 and late.json()["error"]["code"] == "QUEUE_CLOSED"
    assert (await client.get(f"/api/v1/review-queues/{queue_id}")).json()["items"] == 1
    back = await client.get(f"/api/v1/review-queues/{queue_id}/decisions")
    assert back.status_code == 200 and len(back.json()["items"]) == 1


async def test_the_list_filters_by_state_and_refuses_an_unknown_state(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    keep = await external_queue(client)
    gone = await external_queue(client)
    await close(client, gone, headers=MIFORGE)

    async def ids(query: str) -> set[str]:
        r = await client.get(f"/api/v1/review-queues{query}")
        assert r.status_code == 200, r.text
        return {q["id"] for q in r.json()["items"]}

    assert await ids("?state=open") == {keep}
    assert await ids("?state=closed") == {gone}
    assert await ids("") == {keep, gone}
    bad = await client.get("/api/v1/review-queues?state=archived")
    assert bad.status_code == 422


async def test_an_audit_in_progress_cannot_be_closed_by_hand(
    client: httpx.AsyncClient, operator_name: str, label_review_queue: tuple[str, str]
) -> None:
    version, run_id = label_review_queue
    status = await client.post(
        f"/api/v1/versions/{version}/audit", json={"size": 50, "label_run_id": run_id}
    )
    assert status.status_code in (200, 201), status.text
    queue_id = status.json()["queue_id"]
    r = await close(client, queue_id)
    assert r.status_code == 409 and r.json()["error"]["code"] == "AUDIT_IN_PROGRESS"
    assert (await client.get(f"/api/v1/review-queues/{queue_id}")).json()["state"] == "open"


async def test_an_unknown_queue_is_404(client: httpx.AsyncClient, operator_name: str) -> None:
    r = await close(client, "rq_missing")
    assert r.status_code == 404 and r.json()["error"]["code"] == "QUEUE_NOT_FOUND"
