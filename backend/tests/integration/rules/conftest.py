"""Shared fixtures of feature 016's rule-set integration tests (real PostgreSQL, the live app).

Every test runs on BOTH fixture behaviours (``fixture-alpha``: an element that a validator checks,
a proximity cue and a veto; ``fixture-beta``: two words, no element), whose columns and
vocabularies do not agree by construction (FPRD 016 section 12).
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from src.operators import executor
from src.operators import registry as registry_module
from src.operators.registry import OperatorRegistry
from src.services import operator_port
from tests.support.operator_build_driver import RealDriver

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "behaviours"
RS = "/api/v1/rule-sets"
AGENT = {"X-Dataworks-Agent": "agent:dataworks-mcp"}


def fixture_document(behaviour: str) -> dict[str, Any]:
    """A fresh copy of a fixture behaviour's rule set."""
    data: dict[str, Any] = json.loads((FIXTURES / behaviour / "rule_set.json").read_text())
    return copy.deepcopy(data)


@pytest.fixture(autouse=True)
def standalone(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-016.38: every 016 integration test runs as ``test_standalone_without_siblings.py``
    configures it - both sibling URLs unset. 016 calls no sibling."""
    from src.core.config import get_settings

    monkeypatch.setattr(get_settings(), "millm_base_url", None)
    monkeypatch.setattr(get_settings(), "mistudio_base_url", None)


@pytest.fixture(params=["fixture-alpha", "fixture-beta"])
def behaviour(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
def document(behaviour: str) -> dict[str, Any]:
    return fixture_document(behaviour)


async def save(client: httpx.AsyncClient, document: dict[str, Any], **kw: Any) -> httpx.Response:
    return await client.post(RS, json={"document": document}, **kw)


async def saved(client: httpx.AsyncClient, document: dict[str, Any]) -> dict[str, Any]:
    response = await save(client, document)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


@pytest.fixture
def production_driver(monkeypatch: pytest.MonkeyPatch, data_dir: Path) -> Iterator[RealDriver]:
    """``RealDriver`` over the registry production builds (natives only: no catalogue worker)."""
    from src.core.celery_app import celery_app
    from src.workers import version_build_tasks

    reg = OperatorRegistry.build(catalogues=(), entry_points=())
    monkeypatch.setattr(operator_port, "_registry", reg)
    monkeypatch.setattr(registry_module, "_process", reg)
    drv = RealDriver(reg)
    monkeypatch.setattr(executor, "send_task", drv.record_step)
    monkeypatch.setattr(celery_app, "send_task", drv.send_task)
    monkeypatch.setattr(version_build_tasks, "_send_task", drv.send_task)
    monkeypatch.setattr(version_build_tasks, "emit", drv.emit)
    yield drv


_LIVE: set[str] = set()


async def live_tools() -> set[str]:
    """The tool names of the PRODUCTION MCP build path, every default category."""
    if not _LIVE:
        from src.mcp_server.config import DEFAULT_CATEGORIES, MCPSettings
        from src.mcp_server.server import build_server

        mcp, _ = build_server(MCPSettings(tool_categories=DEFAULT_CATEGORIES, auth_token="x" * 32))
        _LIVE.update(t.name for t in await mcp.list_tools())
    return set(_LIVE)


async def assert_refusal(
    response: httpx.Response, status: int, code: str, **named: Any
) -> dict[str, Any]:
    """The ADR-013 envelope with ``code``, each named detail, and a ``next`` naming a live tool."""
    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert error["code"] == code, error
    for key, value in named.items():
        assert error["details"].get(key) == value, (key, error["details"])
    nxt = error["details"].get("next")
    assert nxt is not None, f"{code} carries no next: {error}"
    assert nxt["tool"] in await live_tools(), nxt
    details: dict[str, Any] = error["details"]
    return details
