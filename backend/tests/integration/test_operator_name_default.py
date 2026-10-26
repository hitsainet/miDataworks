"""Who a UI action records without a name in Settings (operator decision D8, 2026-10-16; closes
PADR open question 2): the deployment's ``OPERATOR_NAME`` (default ``operator``); the Settings name
wins when set; an agent records its header identity; both empty refuses with ``NO_IDENTITY``.

The suite's conftest sets ``OPERATOR_NAME=""`` so the refusal path stays the default in tests; each
test here sets the value it needs on the cached settings object, which is what production reads.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from src.core.config import Settings, get_settings
from tests.integration.test_jobs_api import sent  # noqa: F401 - pytest fixture

__all__ = ["sent"]


def test_the_shipped_default_is_operator() -> None:
    assert Settings.model_fields["operator_name"].default == "operator"


@pytest.fixture
def deployment_name(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(get_settings(), "operator_name", "operator")
    return "operator"


async def start(client: httpx.AsyncClient, **headers: str) -> httpx.Response:
    return await client.post("/api/v1/jobs/selftest", json={}, headers=headers)


async def test_without_a_settings_name_the_deployment_name_is_recorded(
    client: httpx.AsyncClient, deployment_name: str, sent: list[dict[str, Any]]
) -> None:
    r = await start(client)
    assert r.status_code in (200, 201, 202), r.text
    assert r.json()["started_by"] == "operator" and r.json()["started_by_origin"] == "operator"
    assert len(sent) == 1


async def test_the_settings_name_wins_over_the_deployment_name(
    client: httpx.AsyncClient, deployment_name: str, sent: list[dict[str, Any]]
) -> None:
    await client.put("/api/v1/settings/operator_name", json={"value": "Ada"})
    r = await start(client)
    assert r.json()["started_by"] == "Ada"


async def test_an_agent_records_its_identity_not_the_operator_name(
    client: httpx.AsyncClient, deployment_name: str, sent: list[dict[str, Any]]
) -> None:
    r = await start(client, **{"X-Dataworks-Agent": "agent:dataworks-mcp"})
    assert r.status_code in (200, 201, 202), r.text
    assert r.json()["started_by"] == "agent:dataworks-mcp"
    assert r.json()["started_by_origin"] == "agent"


async def test_both_empty_still_refuses(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, sent: list[dict[str, Any]]
) -> None:
    monkeypatch.setattr(get_settings(), "operator_name", "   ")
    r = await start(client)
    assert r.status_code == 422 and r.json()["error"]["code"] == "NO_IDENTITY"
    assert sent == []
