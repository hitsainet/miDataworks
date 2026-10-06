# Origin (pattern): miStudio (Onegaishimas/miStudio) backend/tests/unit/test_reachability.py
# @ c829a2cc. Mode: adapt (docs/REUSE.md): the registry / live-app / caller shapes, applied to
# REST routes, Celery tasks and job kinds. Feature 010 extends it to MCP tools.
"""Reachability: a capability is not shipped until a test FAILS when its wiring is removed.

Three shapes, because each catches what the others cannot (miStudio's rule, ADR-021):

1. LIVE REGISTRY. Every expected route is in the app ``create_app()`` actually builds, every
   expected task is in the Celery registry a worker would load, every job kind is in the live
   job-kind registry. Importing a module proves nothing: miStudio shipped sixteen tools that
   every test imported directly while the server never registered them.
2. NO STRAYS. Every live route and task is accounted for here, so a new one cannot ship without
   someone deciding how it is reached.
3. CALLER. A route calls its service with the right payload, exactly once. "Was called" passes
   against a call sending the wrong arguments.

Negative controls are recorded in 0xcc/reviews/foundation_implementation_controls_2026-10-06.md.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from src.core.celery_app import BEAT_SCHEDULE, celery_app
from src.core.job_kinds import JOB_KINDS
from src.main import fastapi_app

#: (method, path) for every public route. The internal emit route is checked separately.
EXPECTED_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/health"),
        ("GET", "/api/v1/jobs"),
        ("GET", "/api/v1/jobs/{job_id}"),
        ("POST", "/api/v1/jobs/{job_id}/cancel"),
        ("POST", "/api/v1/jobs/{job_id}/dismiss"),
        ("POST", "/api/v1/jobs/selftest"),
        ("GET", "/api/v1/settings"),
        ("GET", "/api/v1/settings/{key}"),
        ("PUT", "/api/v1/settings/{key}"),
        ("DELETE", "/api/v1/settings/{key}"),
        ("GET", "/api/v1/endpoint-roles"),
        ("GET", "/api/v1/endpoint-roles/{role}"),
        ("PUT", "/api/v1/endpoint-roles/{role}"),
        ("GET", "/api/v1/endpoint-roles/{role}/models"),
        ("GET", "/api/v1/approvals"),
        ("GET", "/api/v1/approvals/actions"),
        ("GET", "/api/v1/approvals/{approval_id}"),
        ("POST", "/api/v1/approvals/{approval_id}/approve"),
        ("POST", "/api/v1/approvals/{approval_id}/reject"),
    }
)

EXPECTED_TASKS: frozenset[str] = frozenset(
    {
        "midataworks.selftest.run",
        "midataworks.system.janitor",
        "midataworks.system.dispatch_queued",
        "midataworks.system.expire_approvals",
    }
)

EXPECTED_JOB_KINDS: frozenset[str] = frozenset({"selftest", "label_run", "lease"})


def live_routes() -> set[tuple[str, str]]:
    """Read the LIVE app's routes from its OpenAPI document (FastAPI 0.14x wraps included
    routers, so ``app.routes`` is not a flat route list)."""
    fastapi_app.openapi_schema = None
    paths: dict[str, dict[str, Any]] = fastapi_app.openapi()["paths"]
    return {(method.upper(), path) for path, ops in paths.items() for method in ops}


def live_tasks() -> set[str]:
    celery_app.loader.import_default_modules()
    return {name for name in celery_app.tasks if not name.startswith("celery.")}


# --- shape 1 and 2: registries --------------------------------------------------------------


class TestRoutes:
    def test_every_expected_route_is_live(self) -> None:
        missing = EXPECTED_ROUTES - live_routes()
        assert not missing, f"routes not served by the live app: {sorted(missing)}"

    def test_every_live_route_is_accounted_for(self) -> None:
        stray = live_routes() - EXPECTED_ROUTES
        assert not stray, f"live routes no test accounts for: {sorted(stray)}"

    async def test_the_internal_emit_route_is_live(self) -> None:
        """Not in the OpenAPI document by design; probed instead. 403 = served, 404 = not."""
        transport = httpx.ASGITransport(app=fastapi_app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
            response = await http.post("/internal/ws/emit", json={"room": "x", "event": "a:b"})
        assert response.status_code == 403, response.text


class TestCeleryTasks:
    def test_every_expected_task_is_registered(self) -> None:
        missing = EXPECTED_TASKS - live_tasks()
        assert not missing, f"tasks no worker would run: {sorted(missing)}"

    def test_every_registered_task_is_accounted_for(self) -> None:
        stray = live_tasks() - EXPECTED_TASKS
        assert not stray, f"registered tasks no test accounts for: {sorted(stray)}"

    def test_every_beat_entry_names_a_registered_task(self) -> None:
        registered = live_tasks()
        for name, entry in BEAT_SCHEDULE.items():
            assert entry["task"] in registered, f"Beat entry {name} names an unregistered task"
        assert celery_app.conf.beat_schedule.keys() == BEAT_SCHEDULE.keys()

    def test_every_job_kind_task_is_registered(self) -> None:
        registered = live_tasks()
        for kind in JOB_KINDS.values():
            if kind.task_name is not None:
                assert kind.task_name in registered, f"{kind.name} names {kind.task_name}"


class TestJobKinds:
    def test_every_expected_kind_is_registered(self) -> None:
        assert EXPECTED_JOB_KINDS <= set(JOB_KINDS)

    def test_every_registered_kind_is_accounted_for(self) -> None:
        assert set(JOB_KINDS) <= EXPECTED_JOB_KINDS


# --- shape 3: callers (payload and call count) ----------------------------------------------


async def test_health_route_calls_the_health_service_once(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.api.v1.endpoints import health as module

    fake = AsyncMock(return_value={"status": "ok", "dependencies": {}, "resources": {}})
    monkeypatch.setattr(module, "health", fake)
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert fake.await_count == 1
    assert fake.await_args.args == () and fake.await_args.kwargs == {}


async def test_cancel_route_sends_the_job_id_and_reason_once(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, operator_name: str
) -> None:
    created = await client.post("/api/v1/jobs/selftest", json={"duration_seconds": 0})
    job_id = created.json()["id"]
    from src.api.v1.endpoints import jobs as module

    real = module.JobService.cancel
    spy = AsyncMock(side_effect=real)
    monkeypatch.setattr(module.JobService, "cancel", spy)
    await client.post(f"/api/v1/jobs/{job_id}/cancel", json={"reason": "stop it"})
    assert spy.await_count == 1
    assert spy.await_args.args[1:] == (job_id, "stop it")


async def test_selftest_route_creates_one_job_with_the_request(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, operator_name: str
) -> None:
    from src.api.v1.endpoints import jobs as module

    spy = AsyncMock(side_effect=module.JobService.create)
    monkeypatch.setattr(module.JobService, "create", spy)
    monkeypatch.setattr(module, "_dispatch", lambda: [])
    response = await client.post(
        "/api/v1/jobs/selftest",
        json={"duration_seconds": 1, "rows": 7, "required_model_id": "model-a"},
    )
    assert response.status_code == 201, response.text
    assert spy.await_count == 1
    kwargs = spy.await_args.kwargs
    assert kwargs["kind"] == "selftest"
    assert kwargs["params"]["rows"] == 7 and kwargs["params"]["duration_seconds"] == 1
    assert kwargs["required_model_id"] == "model-a"
    assert kwargs["started_by"] == operator_name and kwargs["origin"] == "operator"


async def test_settings_put_writes_the_key_and_value_once(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.api.v1.endpoints import settings as module

    spy = AsyncMock(side_effect=module.AppSettingService.upsert)
    monkeypatch.setattr(module.AppSettingService, "upsert", spy)
    response = await client.put("/api/v1/settings/operator_name", json={"value": "Ada"})
    assert response.status_code == 200, response.text
    assert spy.await_count == 1
    assert spy.await_args.args[1:] == ("operator_name", "Ada")


async def test_endpoint_role_put_writes_the_role_once(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.api.v1.endpoints import endpoint_roles as module

    spy = AsyncMock(side_effect=module.EndpointRoleService.update)
    monkeypatch.setattr(module.EndpointRoleService, "update", spy)
    response = await client.put(
        "/api/v1/endpoint-roles/judge",
        json={"protocol": "openai_chat", "base_url": "http://judge.test/v1", "model_id": "q"},
    )
    assert response.status_code == 200, response.text
    assert spy.await_count == 1
    role, update = spy.await_args.args[1:]
    assert role == "judge" and update.base_url == "http://judge.test/v1"
    assert update.model_id == "q" and update.api_key is None


async def test_fetch_models_route_asks_the_service_once(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.api.v1.endpoints import endpoint_roles as module
    from src.clients.model_listing import ModelListing

    fake = AsyncMock(return_value=ModelListing(["m1"], "openai", "http://x/v1/models"))
    monkeypatch.setattr(module.EndpointRoleService, "fetch_models", fake)
    response = await client.get(
        "/api/v1/endpoint-roles/classifier/models", params={"base_url": "http://x"}
    )
    assert response.json()["models"] == ["m1"]
    assert fake.await_count == 1
    assert fake.await_args.args[1:] == ("classifier", "http://x")


async def test_approve_route_calls_the_service_once_with_the_operator(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, operator_name: str
) -> None:
    from src.api.v1.endpoints import approvals as module

    pending = await client.put(
        "/api/v1/settings/hf_token",
        json={"value": "hf_ReachabilityToken_000000000"},
        headers={"X-Dataworks-Agent": "agent:dataworks-mcp"},
    )
    approval_id = pending.json()["approval_id"]
    spy = AsyncMock(side_effect=module.ApprovalService.approve)
    monkeypatch.setattr(module.ApprovalService, "approve", spy)
    response = await client.post(f"/api/v1/approvals/{approval_id}/approve")
    assert response.status_code == 200, response.text
    assert spy.await_count == 1
    assert spy.await_args.args[1:] == (approval_id, operator_name)
