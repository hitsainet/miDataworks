"""``GET /api/v1/agent-access``: the Agent access card's data (010 FTASKS 11.1; FTID 5.6)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from src.api.v1.endpoints import agent_access
from src.core.config import get_settings
from src.services.agent_activity_service import count_sessions

AGENT = {"X-Dataworks-Agent": "agent:dataworks-mcp"}


async def test_the_card_states_the_deployment_values_when_mcp_is_unreachable(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "mcp_internal_url", "http://127.0.0.1:9")
    monkeypatch.setattr(get_settings(), "mcp_public_url", "http://mcp-dataworks.hitsai.local/mcp")
    body = (await client.get("/api/v1/agent-access")).json()
    assert body["mcp_public_url"] == "http://mcp-dataworks.hitsai.local/mcp"
    assert body["mcp"]["reachable"] is False and body["mcp"]["categories"] is None
    assert body["mcp"]["reason"].startswith("unreachable")
    assert body["label_threshold"] == 5000 and body["label_window_hours"] == 24
    assert set(body["gated_actions"]) == {
        "hub_push",
        "agent_label_rows",
        "version_delete",
        "millm_model_load",
        "secret_write",
        "source_annotate",
        "gate_target_write",
    }


async def test_an_unset_internal_url_says_so(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "mcp_internal_url", None)
    body = (await client.get("/api/v1/agent-access")).json()
    assert body["mcp"] == {
        "reachable": False,
        "categories": None,
        "reason": "MCP_INTERNAL_URL is not set",
    }


async def test_the_categories_come_from_the_mcp_health(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = agent_access.read_mcp_health

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(200, json={"categories": ["datasets", "core"], "status": "ok"})

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def patched(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(agent_access.httpx, "AsyncClient", patched)
    result = await real("http://midataworks-mcp:8765")
    assert result["reachable"] is True and result["categories"] == ["core", "datasets"]


async def test_activity_counts_identities_sessions_and_requests_in_the_last_hour(
    client: httpx.AsyncClient,
) -> None:
    for _ in range(3):
        await client.get("/api/v1/jobs", headers=AGENT)
    await client.get("/api/v1/jobs", headers={"X-Dataworks-Agent": "agent:mistudio-mcp"})
    activity = (await client.get("/api/v1/agent-access")).json()["activity"]
    assert activity["requests"] == 4
    assert activity["identities"] == ["agent:dataworks-mcp", "agent:mistudio-mcp"]
    assert activity["sessions"] == 2


def test_a_gap_over_thirty_minutes_starts_a_new_session() -> None:
    t0 = datetime(2026, 10, 7, tzinfo=UTC)
    rows = [
        ("agent:a", t0),
        ("agent:a", t0 + timedelta(minutes=30)),
        ("agent:a", t0 + timedelta(minutes=61)),
        ("agent:b", t0 + timedelta(minutes=5)),
    ]
    assert count_sessions(rows) == 3
