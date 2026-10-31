"""Shared fixtures of feature 018's code-rule integration tests (real PostgreSQL and Redis, the live
app, a recording fake runner behind the real client).

Every test runs standalone (both sibling URLs unset; FR-018.48) with code rules switched on, unless
it is the test of the switch. Fixture behaviours ``fixture-gamma`` and ``fixture-delta`` share no
vocabulary (``tests/support/code_rule_fixtures.py``).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from src.core.database import get_sync_engine
from tests.support import code_rule_fixtures as fx
from tests.support.fake_runner import FakeRunner, installed

CR = "/api/v1/code-rules"
AGENT = {"X-Dataworks-Agent": "agent:dataworks-mcp"}
SAVE_KEY = "src.api.v1.endpoints.code_rules.save_code_rule"


@pytest.fixture(autouse=True)
def standalone_and_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core.config import get_settings

    monkeypatch.setattr(get_settings(), "millm_base_url", None)
    monkeypatch.setattr(get_settings(), "mistudio_base_url", None)
    monkeypatch.setattr(get_settings(), "code_rules_enabled", True)


@pytest.fixture(autouse=True)
def own_rate_window(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """Each test counts dry runs in its own Redis key, so no other test's calls fill its window."""
    import uuid

    from src.services.code_rules import limiter

    monkeypatch.setattr(limiter, "KEY", f"dw:test:{uuid.uuid4().hex}:{{minute}}")


@pytest.fixture
def fake() -> Iterator[FakeRunner]:
    with installed(FakeRunner(fx.twins_by_hash())) as runner:
        yield runner


@pytest.fixture(params=["fixture-gamma", "fixture-delta"])
def behaviour(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
def executor_spy() -> Iterator[list[dict[str, Any]]]:
    """Every replay of the save by the approval executor, with its payload (call count)."""
    from src.core.agent_origin import EXECUTORS

    calls: list[dict[str, Any]] = []
    original = EXECUTORS[SAVE_KEY]

    async def spy(**kwargs: Any) -> Any:
        calls.append(
            {
                "body": kwargs["body"].model_dump(mode="json"),
                "approval_id": kwargs["actor"].approval_id,
            }
        )
        return await original.fn(**kwargs)

    EXECUTORS[SAVE_KEY] = dataclasses.replace(original, fn=spy)
    try:
        yield calls
    finally:
        EXECUTORS[SAVE_KEY] = original


def count(table: str, where: str = "true", **params: Any) -> int:
    with get_sync_engine().connect() as conn:
        return int(
            conn.execute(
                text(f"SELECT count(*) FROM {table} WHERE {where}"), params  # noqa: S608
            ).scalar_one()
        )


async def save(
    client: httpx.AsyncClient, body: dict[str, Any], agent: bool = False
) -> httpx.Response:
    return await client.post(CR, json=body, headers=AGENT if agent else None)


async def approve(client: httpx.AsyncClient, approval_id: str) -> dict[str, Any]:
    response = await client.post(f"/api/v1/approvals/{approval_id}/approve")
    assert response.status_code == 200, response.text
    out: dict[str, Any] = response.json()
    return out


_LIVE: set[str] = set()


async def live_tools() -> set[str]:
    """The tool names of the PRODUCTION MCP build path, every default category."""
    if not _LIVE:
        from src.mcp_server.config import DEFAULT_CATEGORIES, MCPSettings
        from src.mcp_server.server import build_server

        mcp, _ = build_server(MCPSettings(tool_categories=DEFAULT_CATEGORIES, auth_token="x" * 32))
        _LIVE.update(t.name for t in await mcp.list_tools())
    return set(_LIVE)


# --- slice 4: builds through the REAL executor, rule sets naming a code rule -------------------

RS = "/api/v1/rule-sets"


@pytest.fixture
def production_driver(monkeypatch: pytest.MonkeyPatch, data_dir: Any) -> Iterator[Any]:
    """``RealDriver`` over the registry production builds (natives only), recording every
    dispatched step's payload in ``driver.dispatched`` (payload and call count)."""
    from src.core.celery_app import celery_app
    from src.operators import executor
    from src.operators import registry as registry_module
    from src.operators.registry import OperatorRegistry
    from src.services import operator_port
    from src.workers import version_build_tasks
    from tests.support.operator_build_driver import RealDriver

    reg = OperatorRegistry.build(catalogues=(), entry_points=())
    monkeypatch.setattr(operator_port, "_registry", reg)
    monkeypatch.setattr(registry_module, "_process", reg)
    drv = RealDriver(reg)
    dispatched: list[dict[str, Any]] = []

    def record(name: str, **options: Any) -> None:
        dispatched.append(dict(options["args"][0]))
        drv.record_step(name, **options)

    drv.dispatched = dispatched  # type: ignore[attr-defined]
    monkeypatch.setattr(executor, "send_task", record)
    monkeypatch.setattr(celery_app, "send_task", drv.send_task)
    monkeypatch.setattr(version_build_tasks, "_send_task", drv.send_task)
    monkeypatch.setattr(version_build_tasks, "emit", drv.emit)
    yield drv


async def approved_rule(client: httpx.AsyncClient, behaviour: str) -> dict[str, Any]:
    """The fixture code rule saved by the operator (so approved), as the save answers it."""
    response = await save(client, fx.save_body(behaviour))
    assert response.status_code == 201, response.text
    out: dict[str, Any] = response.json()
    assert out["approval_state"] == "approved"
    return out


async def saved_rule_set(client: httpx.AsyncClient, document: dict[str, Any]) -> dict[str, Any]:
    response = await client.post(RS, json={"document": document})
    assert response.status_code == 201, response.text
    out: dict[str, Any] = response.json()
    return out


async def revoke(client: httpx.AsyncClient, sha: str, reason: str = "fixture revoke") -> None:
    response = await client.post(f"{CR}/approvals/{sha}/revoke", json={"reason": reason})
    assert response.status_code == 200, response.text
