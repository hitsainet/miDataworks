"""The miForge review API (006 FTASKS 10.1 – 10.4; FR-006.30, FR-006.31)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

MIFORGE = {"X-Dataworks-Agent": "agent:miforge"}


def candidate(i: int, **extra: Any) -> dict[str, Any]:
    return {
        "external_id": f"cand-{i}",
        "prompt": f"Write a joke {i}",
        "completion": f"Joke {i} <script>alert(1)</script>",
        "provenance": {"run_id": "mf_run_1", "batch": 1, "step": i},
        **extra,
    }


@pytest.fixture
async def queue_id(client: httpx.AsyncClient) -> str:
    r = await client.post(
        "/api/v1/review-queues",
        json={
            "kind": "external",
            "question": "Admit this sample?",
            "label_set": ["admit", "drop"],
            "external_ref": {"run_id": "mf_run_1"},
        },
        headers=MIFORGE,
    )
    assert r.status_code == 201, r.text
    assert r.json()["origin_app"] == "miforge" and r.json()["created_by"] == "agent:miforge"
    return str(r.json()["id"])


async def post(
    client: httpx.AsyncClient, queue_id: str, cands: list[dict[str, Any]], headers: Any = MIFORGE
) -> httpx.Response:
    return await client.post(
        f"/api/v1/review-queues/{queue_id}/candidates", json={"candidates": cands}, headers=headers
    )


async def test_external_queue_needs_the_header(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    r = await client.post(
        "/api/v1/review-queues", json={"kind": "external", "question": "q", "label_set": ["a", "b"]}
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "ORIGIN_APP_REQUIRED"


async def test_candidates_are_idempotent_on_external_id(
    client: httpx.AsyncClient, queue_id: str
) -> None:
    first = await post(client, queue_id, [candidate(1), candidate(2)])
    assert first.status_code == 201, first.text
    assert [x["created"] for x in first.json()["items"]] == [True, True]
    again = await post(client, queue_id, [candidate(1), candidate(3)])
    items = again.json()["items"]
    assert (
        items[0]["created"] is False and items[0]["item_id"] == first.json()["items"][0]["item_id"]
    )
    assert items[1]["created"] is True
    queue = (await client.get(f"/api/v1/review-queues/{queue_id}")).json()
    assert queue["items"] == 3
    page = (await client.get(f"/api/v1/review-queues/{queue_id}/items")).json()["items"]
    assert page[0]["payload"]["completion"].endswith("<script>alert(1)</script>")  # data, as text


async def test_candidates_without_the_header_are_refused(
    client: httpx.AsyncClient, queue_id: str
) -> None:
    r = await post(client, queue_id, [candidate(1)], headers={})
    assert r.status_code == 400 and r.json()["error"]["code"] == "ORIGIN_APP_REQUIRED"


async def test_another_application_cannot_post_to_the_queue(
    client: httpx.AsyncClient, queue_id: str
) -> None:
    r = await post(client, queue_id, [candidate(1)], headers={"X-Dataworks-Agent": "agent:other"})
    assert r.status_code == 403


async def test_payload_caps(
    client: httpx.AsyncClient, queue_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.core.config import get_settings

    monkeypatch.setattr(get_settings(), "review_candidate_max_bytes", 300)
    big = await post(client, queue_id, [candidate(1, completion="x" * 400)])
    assert big.status_code == 413 and big.json()["error"]["details"]["external_id"] == "cand-1"
    many = await post(client, queue_id, [candidate(i) for i in range(501)])
    assert many.status_code == 413 and many.json()["error"]["details"]["max"] == 500


async def test_operator_reject_is_read_back_and_agent_reject_is_refused(
    client: httpx.AsyncClient, operator_name: str, queue_id: str
) -> None:
    ids = {
        x["external_id"]: x["item_id"]
        for x in (await post(client, queue_id, [candidate(1), candidate(2)])).json()["items"]
    }
    agent = await client.post(
        f"/api/v1/review-items/{ids['cand-1']}/decisions",
        json={"decision": "reject", "reason": "bad"},
        headers=MIFORGE,
    )
    assert (
        agent.status_code == 403 and agent.json()["error"]["code"] == "AGENT_DECISION_NOT_ALLOWED"
    )
    rejected = await client.post(
        f"/api/v1/review-items/{ids['cand-1']}/decisions",
        json={"decision": "reject", "reason": "off-topic"},
    )
    assert rejected.status_code == 201, rejected.text
    accepted = await client.post(
        f"/api/v1/review-items/{ids['cand-2']}/decisions", json={"decision": "accept"}
    )
    back = (await client.get(f"/api/v1/review-queues/{queue_id}/decisions")).json()["items"]
    assert [(d["external_id"], d["decision"]) for d in back] == [
        ("cand-1", "reject"),
        ("cand-2", "accept"),
    ]
    assert back[0]["decision_id"] == rejected.json()["id"]
    only = (
        await client.get(
            f"/api/v1/review-queues/{queue_id}/decisions", params={"external_ids": ["cand-2"]}
        )
    ).json()
    assert [d["decision_id"] for d in only["items"]] == [accepted.json()["id"]]
    since = (
        await client.get(
            f"/api/v1/review-queues/{queue_id}/decisions", params={"since": back[0]["created_at"]}
        )
    ).json()
    assert [d["external_id"] for d in since["items"]] == ["cand-2"]
