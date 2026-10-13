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
        ("GET", "/api/v1/agent-access"),
    }
)

#: Feature 002's routes; the caller shape for each is in test_routes_reachable_002.py.
EXPECTED_ROUTES_002: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/v1/datasets/meta"),
        ("GET", "/api/v1/datasets"),
        ("POST", "/api/v1/datasets"),
        ("GET", "/api/v1/datasets/{dataset_id}"),
        ("PATCH", "/api/v1/datasets/{dataset_id}"),
        ("GET", "/api/v1/recipes"),
        ("POST", "/api/v1/recipes"),
        ("POST", "/api/v1/recipes/validate"),
        ("POST", "/api/v1/recipes/import"),
        ("GET", "/api/v1/recipes/{recipe_id}"),
        ("POST", "/api/v1/recipes/{recipe_id}/revisions"),
        ("POST", "/api/v1/recipes/{recipe_id}/clone"),
        ("POST", "/api/v1/recipes/{recipe_id}/archive"),
        ("GET", "/api/v1/recipes/{recipe_id}/revisions/{revision_id}/export"),
        ("GET", "/api/v1/recipe-drafts"),
        ("POST", "/api/v1/recipe-drafts"),
        ("GET", "/api/v1/recipe-drafts/{draft_id}"),
        ("PUT", "/api/v1/recipe-drafts/{draft_id}"),
        ("DELETE", "/api/v1/recipe-drafts/{draft_id}"),
        ("POST", "/api/v1/recipe-drafts/{draft_id}/save"),
        ("POST", "/api/v1/recipes/{recipe_id}/build"),
        ("POST", "/api/v1/versions"),
        ("GET", "/api/v1/versions"),
        ("GET", "/api/v1/versions/{version_id}"),
        ("GET", "/api/v1/versions/{version_id}/manifest"),
        ("GET", "/api/v1/versions/{version_id}/rows"),
        ("GET", "/api/v1/versions/{version_id}/rows/history"),
        ("GET", "/api/v1/versions/{version_id}/drop-log"),
        ("GET", "/api/v1/versions/{version_id}/events"),
        ("GET", "/api/v1/versions/{version_id}/lineage"),
        ("GET", "/api/v1/versions/{version_id}/compare"),
        ("POST", "/api/v1/versions/{version_id}/verify-rebuild"),
        ("DELETE", "/api/v1/versions/{version_id}"),
    }
)

#: Feature 001's routes; the caller shape is in test_routes_reachable_001.py.
EXPECTED_ROUTES_001: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/v1/sources/meta"),
        ("POST", "/api/v1/sources/hf/preview"),
        ("POST", "/api/v1/sources/hf"),
        ("POST", "/api/v1/sources/uploads"),
        ("GET", "/api/v1/sources"),
        ("GET", "/api/v1/sources/{source_id}"),
        ("GET", "/api/v1/sources/{source_id}/rows"),
        ("POST", "/api/v1/sources/{source_id}/annotations"),
        ("DELETE", "/api/v1/sources/{source_id}"),
    }
)

#: Feature 003's routes; each is called with payload and status in
#: tests/integration/operators/test_api_operators.py.
EXPECTED_ROUTES_003: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/v1/operators"),
        ("GET", "/api/v1/operators/schema-subset"),
        ("GET", "/api/v1/operators/allowlist"),
        ("POST", "/api/v1/operators/allowlist"),
        ("POST", "/api/v1/operators/allowlist/revoke"),
        ("POST", "/api/v1/operators/upgrade-plan"),
        ("GET", "/api/v1/operators/previews/{preview_id}"),
        ("GET", "/api/v1/operators/{name}"),
        ("GET", "/api/v1/operators/{name}/{version}"),
        ("POST", "/api/v1/operators/{name}/{version}/validate"),
        ("POST", "/api/v1/operators/{name}/{version}/preview"),
        ("POST", "/api/v1/operators/{name}/{version}/statistics"),
    }
)

#: Feature 008's routes; the caller shape is in tests/integration/publishing/test_routes_reachable_008.py.
EXPECTED_ROUTES_008: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/api/v1/versions/{version_id}/publish-builds"),
        ("GET", "/api/v1/publish-builds/{build_id}"),
        ("POST", "/api/v1/versions/{version_id}/publish-checks"),
        ("GET", "/api/v1/publish-check-runs/{check_run_id}"),
        ("GET", "/api/v1/versions/{version_id}/card-draft"),
        ("GET", "/api/v1/versions/{version_id}/handoff-manifest"),
        ("POST", "/api/v1/publishes"),
        ("GET", "/api/v1/publishes"),
        ("GET", "/api/v1/publishes/{publish_id}"),
        ("POST", "/api/v1/publishes/{publish_id}/card"),
        ("POST", "/api/v1/publishes/{publish_id}/reverify"),
        ("GET", "/api/v1/licence-table"),
        ("POST", "/api/v1/exports"),
        ("GET", "/api/v1/exports"),
        ("GET", "/api/v1/exports/{export_id}"),
        ("GET", "/api/v1/exports/{export_id}/files/{name}"),
        ("GET", "/api/v1/model-terms/{model_id}"),
        ("POST", "/api/v1/model-terms/{model_id}/notes"),
        ("POST", "/api/v1/config-versions"),
        ("GET", "/api/v1/config-versions"),
        ("GET", "/api/v1/config-versions/{config_id}"),
    }
)

#: Feature 005's routes (FTDD 005 section 5.1); each is called with payload and status in
#: tests/integration/labeling/ and its caller shape in test_labeling_reachability.py.
EXPECTED_ROUTES_005: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/v1/decision-templates"),
        ("POST", "/api/v1/decision-templates"),
        ("POST", "/api/v1/decision-templates/import"),
        ("GET", "/api/v1/decision-templates/{template_id}"),
        ("POST", "/api/v1/decision-templates/{template_id}/clone"),
        ("GET", "/api/v1/decision-templates/{template_id}/export"),
        ("GET", "/api/v1/rubrics"),
        ("POST", "/api/v1/rubrics"),
        ("POST", "/api/v1/rubrics/import"),
        ("GET", "/api/v1/rubrics/{rubric_id}"),
        ("POST", "/api/v1/rubrics/{rubric_id}/clone"),
        ("GET", "/api/v1/rubrics/{rubric_id}/export"),
        ("POST", "/api/v1/endpoint-roles/{role}/test"),
        ("POST", "/api/v1/labeling/sample"),
        ("POST", "/api/v1/labeling/keep-share"),
        ("GET", "/api/v1/labeling/keep-share/{job_id}"),
        # 009 probe-verdict runs (operator decision 2026-10-07): the probes miLLM holds.
        ("GET", "/api/v1/labeling/probes"),
        ("GET", "/api/v1/label-runs/plan"),
        ("POST", "/api/v1/label-runs"),
        ("GET", "/api/v1/label-runs"),
        ("POST", "/api/v1/label-runs/aggregate"),
        ("GET", "/api/v1/label-runs/{run_id}"),
        ("GET", "/api/v1/label-runs/{run_id}/labels"),
        ("POST", "/api/v1/label-runs/{run_id}/cancel"),
        ("POST", "/api/v1/label-runs/{run_id}/resume"),
        ("POST", "/api/v1/label-runs/{run_id}/rederive"),
    }
)

#: Feature 006's routes (FTDD 006 section 5.1); the caller shapes are in
#: tests/unit/calibration/test_calibration_reachability.py and
#: tests/unit/review/test_review_reachability.py.
EXPECTED_ROUTES_006: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/api/v1/calibration-sets/preview"),
        ("POST", "/api/v1/calibration-sets/import"),
        ("POST", "/api/v1/calibration-sets/from-review"),
        ("GET", "/api/v1/calibration-sets"),
        ("GET", "/api/v1/calibration-sets/{set_id}"),
        ("POST", "/api/v1/calibration-records"),
        ("GET", "/api/v1/calibration-records"),
        ("GET", "/api/v1/calibration-records/{record_id}"),
        ("GET", "/api/v1/calibration-status"),
        ("GET", "/api/v1/calibration-targets"),
        ("PUT", "/api/v1/calibration-targets"),
        ("GET", "/api/v1/review-queues"),
        ("POST", "/api/v1/review-queues"),
        ("GET", "/api/v1/review-queues/{queue_id}"),
        ("GET", "/api/v1/review-queues/{queue_id}/items"),
        ("POST", "/api/v1/review-items/{item_id}/decisions"),
        ("GET", "/api/v1/review-items/{item_id}/decisions"),
        ("POST", "/api/v1/review-queues/{queue_id}/candidates"),
        ("GET", "/api/v1/review-queues/{queue_id}/decisions"),
        ("POST", "/api/v1/versions/{version_id}/audit"),
        ("GET", "/api/v1/versions/{version_id}/audit"),
    }
)

#: Feature 004's routes; each is called with payload and status in
#: tests/integration/curation/test_curation_routes.py (and test_curation_reachability.py).
EXPECTED_ROUTES_004: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/api/v1/versions/{version_id}/shortcut-audit"),
        ("GET", "/api/v1/versions/{version_id}/shortcut-audit"),
        ("GET", "/api/v1/versions/{version_id}/shortcut-audit/cells"),
        ("GET", "/api/v1/datasets/{dataset_id}/shortcut-level"),
        ("PUT", "/api/v1/datasets/{dataset_id}/shortcut-level"),
        ("DELETE", "/api/v1/datasets/{dataset_id}/shortcut-level"),
        ("GET", "/api/v1/settings/shortcut-level"),
        ("PUT", "/api/v1/settings/shortcut-level"),
        ("POST", "/api/v1/versions/{version_id}/profile"),
        ("GET", "/api/v1/versions/{version_id}/profile"),
        ("POST", "/api/v1/versions/{version_id}/leakage"),
        ("GET", "/api/v1/versions/{version_id}/leakage"),
        ("GET", "/api/v1/versions/{version_id}/leakage/pairs"),
        ("POST", "/api/v1/versions/{version_id}/contamination"),
        ("GET", "/api/v1/versions/{version_id}/contamination"),
        ("POST", "/api/v1/versions/{version_id}/trl-validation"),
        ("GET", "/api/v1/curation/benchmarks"),
    }
)

EXPECTED_ROUTES_009: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/api/v1/detector-sets"),
        ("GET", "/api/v1/detector-sets"),
        ("GET", "/api/v1/detector-sets/{set_id}"),
        ("PATCH", "/api/v1/detector-sets/{set_id}"),
        ("POST", "/api/v1/detector-sets/{set_id}/archive"),
        ("POST", "/api/v1/detector-sets/{set_id}/checks"),
        ("POST", "/api/v1/detector-sets/{set_id}/send"),
        ("GET", "/api/v1/detector-sets/{set_id}/sends"),
        ("GET", "/api/v1/detector-sends/{send_id}"),
        ("POST", "/api/v1/detector-sends/{send_id}/resume"),
        ("POST", "/api/v1/detector-sends/{send_id}/cancel"),
        ("POST", "/api/v1/detector-sets/{set_id}/results/refresh"),
        ("GET", "/api/v1/detector-sets/{set_id}/results"),
        ("POST", "/api/v1/reward-marks"),
        ("GET", "/api/v1/reward-marks"),
        ("POST", "/api/v1/agreement-reports"),
        ("GET", "/api/v1/agreement-reports/{report_id}"),
        # reproduction links (FR-009.77 option (b), 2026-10-07); payloads and statuses are
        # asserted in tests/integration/detector_sets/test_reproduction_links.py
        ("POST", "/api/v1/reproduction-links"),
        ("GET", "/api/v1/reproduction-links"),
        ("GET", "/api/v1/reproduction-links/{link_id}"),
        ("DELETE", "/api/v1/reproduction-links/{link_id}"),
        # minimal pairs as a chain (operator decision 2026-10-07); payloads and statuses are
        # asserted in tests/integration/minimal_pairs/test_minimal_pair_chain.py
        ("POST", "/api/v1/minimal-pair-chains/plan"),
        ("POST", "/api/v1/minimal-pair-chains"),
        ("GET", "/api/v1/minimal-pair-chains"),
        ("GET", "/api/v1/minimal-pair-chains/{chain_id}"),
        ("POST", "/api/v1/minimal-pair-chains/{chain_id}/resume"),
        ("POST", "/api/v1/minimal-pair-chains/{chain_id}/cancel"),
    }
)

#: Feature 007's routes; each is called with payload and status in
#: tests/integration/generation/ (test_plan_refusals.py, test_standard_run.py and the others).
EXPECTED_ROUTES_007: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/v1/generation-templates"),
        ("POST", "/api/v1/generation-templates"),
        ("GET", "/api/v1/generation-templates/{template_id}"),
        ("POST", "/api/v1/generation-templates/{template_id}/clone"),
        ("POST", "/api/v1/generation-runs/plan"),
        ("POST", "/api/v1/generation-runs"),
        ("GET", "/api/v1/generation-runs"),
        ("POST", "/api/v1/generation-runs/preview"),
        ("POST", "/api/v1/generation-runs/independence-check"),
        ("POST", "/api/v1/steering-settings/compare"),
        ("GET", "/api/v1/generation-runs/{run_id}"),
        ("GET", "/api/v1/generation-runs/{run_id}/records"),
        ("GET", "/api/v1/generation-runs/{run_id}/pairs"),
        ("POST", "/api/v1/generation-runs/{run_id}/cancel"),
        ("POST", "/api/v1/generation-runs/{run_id}/resume"),
        ("POST", "/api/v1/generation-runs/{run_id}/candidate-build"),
        ("GET", "/api/v1/versions/{version_id}/diversity"),
        ("POST", "/api/v1/versions/{version_id}/diversity"),
    }
)

EXPECTED_ROUTES = (
    EXPECTED_ROUTES
    | EXPECTED_ROUTES_009
    | EXPECTED_ROUTES_007
    | EXPECTED_ROUTES_002
    | EXPECTED_ROUTES_001
    | EXPECTED_ROUTES_003
    | EXPECTED_ROUTES_008
    | EXPECTED_ROUTES_005
    | EXPECTED_ROUTES_006
    | EXPECTED_ROUTES_004
)

EXPECTED_TASKS: frozenset[str] = frozenset(
    {
        "midataworks.selftest.run",
        "midataworks.system.janitor",
        "midataworks.system.dispatch_queued",
        "midataworks.system.expire_approvals",
        "midataworks.system.prune_agent_requests",
        "midataworks.versions.advance_build",
        "midataworks.versions.verify_rebuild",
        "midataworks.versions.version_orphan_sweeper",
        "midataworks.sources.preview_hf",
        "midataworks.sources.import_source",
        "midataworks.operators.step.curation",
        "midataworks.operators.step.labeling",
        "midataworks.operators.step.finalize_datajuicer",
        "midataworks.operators.step.finalize_designer",
        "midataworks.operators.preview",
        "midataworks.publish.build",
        "midataworks.publish.check",
        "midataworks.publish.publish",
        "midataworks.publish.reverify",
        "midataworks.publish.export",
        "midataworks.labeling.run_label_run",
        "midataworks.labeling.run_label_preview",
        "midataworks.labeling.run_rederive",
        "midataworks.labeling.run_aggregate",
        "midataworks.labeling.renew_model_leases",
        "midataworks.calibration.compute_record",
        "midataworks.detector_sets.run_mistudio_send",
        "midataworks.curation.run_report",
        "midataworks.curation.post_version",
        "midataworks.curation.sweep_unaudited",
        "midataworks.generation.run",
        "midataworks.generation.diversity_report",
        "midataworks.generation.diversity_sweep",
        "midataworks.detector_sets.advance_minimal_pair_chains",
    }
)

EXPECTED_JOB_KINDS: frozenset[str] = frozenset(
    {
        "selftest",
        "mistudio_send",
        "label_run",
        "generation_run",
        "diversity_report",
        "lease",
        "version_build",
        "version_verify",
        "source_import",
        "publish_build",
        "publish_check",
        "publish",
        "publish_reverify",
        "export",
        "label_preview",
        "label_rederive",
        "label_aggregate",
        "calibration_compute",
        "curation_report",
    }
)


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


def test_the_version_orphan_sweeper_is_scheduled_hourly() -> None:
    """FTASKS 19.2. A sweeper nothing schedules leaves every orphan directory on disk forever."""
    from datetime import timedelta

    entries = [
        e
        for e in celery_app.conf.beat_schedule.values()
        if e["task"] == "midataworks.versions.version_orphan_sweeper"
    ]
    assert len(entries) == 1
    assert entries[0]["schedule"] == timedelta(hours=1)


def test_every_gated_route_publishes_its_approval_action_in_openapi() -> None:
    """010's MCP server reads ``x-approval-action``; a gated route without it would be presented
    as one that runs at once. Derived from the live routes, never a hand-kept list."""
    from fastapi.routing import APIRoute

    from src.api.v1.router import ROUTERS
    from src.main import fastapi_app

    paths = fastapi_app.openapi()["paths"]
    # FastAPI 0.142 wraps included routers, so app.routes is not flat; read the registry.
    routes = [r for router in ROUTERS for r in router.routes if isinstance(r, APIRoute)]
    gated = [
        (route, route.endpoint.__dw_approval_action__)  # type: ignore[attr-defined]
        for route in routes
        if hasattr(route.endpoint, "__dw_approval_action__")
    ]
    assert len(gated) >= 6, "settings, endpoint keys, version delete/build, sources"
    for route, action in gated:
        for method in route.methods:
            assert paths[route.path][method.lower()].get("x-approval-action") == action, (
                route.path,
                method,
            )
    ungated = [
        (route.path, m)
        for route in routes
        if not hasattr(route.endpoint, "__dw_approval_action__")
        for m in route.methods
    ]
    for path, method in ungated:
        assert "x-approval-action" not in paths.get(path, {}).get(method.lower(), {}), path


# ============================================================================================
# MCP tools (feature 010; FTID section 8.1). Six shapes: registry, built server, caller,
# recording-client fidelity (tests/unit/mcp/test_client.py), gate, deployment, accounting.
# ============================================================================================

import asyncio as _asyncio  # noqa: E402
import re as _re  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

from src.mcp_server.config import DEFAULT_CATEGORIES, VALID_CATEGORIES, MCPSettings  # noqa: E402
from src.mcp_server.server import build_server, tool_names_declared  # noqa: E402
from src.mcp_server.tools import CATEGORY_MODULES  # noqa: E402
from tests.support.mcp_harness import FakeGate, build_harness, call_tool  # noqa: E402
from tests.support.mcp_ledger import (  # noqa: E402
    CALLER_ASSERTION_EXEMPT,
    EXPECTED_CALLS,
    PENDING_ROUTES,
    pending_tools,
)

_REPO = _Path(__file__).resolve().parents[3]


def _declared(category: str) -> set[str]:
    return {n for m in CATEGORY_MODULES[category] for n in tool_names_declared(m)}


def _built(categories: str) -> set[str]:
    """The PRODUCTION build path, never a hand-called register()."""
    mcp, _ = build_server(MCPSettings(tool_categories=categories, auth_token="x" * 32))
    return {t.name for t in _asyncio.run(mcp.list_tools())}


def _categories_with_no_route_yet() -> set[str]:
    """Categories every route of which is pending (their module registers nothing yet)."""
    return {c for c in CATEGORY_MODULES if not _declared(c)}


class TestMcpRegistry:
    def test_every_category_is_in_the_registry_and_selectable(self) -> None:
        for category in CATEGORY_MODULES:
            assert category in VALID_CATEGORIES, category

    def test_every_module_exposes_register(self) -> None:
        for category, modules in CATEGORY_MODULES.items():
            for module in modules:
                assert callable(getattr(module, "register", None)), (category, module)


class TestMcpBuiltServer:
    @pytest.mark.parametrize("category", sorted(CATEGORY_MODULES))
    def test_the_production_build_registers_exactly_the_category_tools(self, category: str) -> None:
        assert _built(category) == _declared(category)

    @pytest.mark.parametrize("category", sorted(CATEGORY_MODULES))
    def test_a_disabled_category_contributes_nothing(self, category: str) -> None:
        others = ",".join(sorted(set(CATEGORY_MODULES) - {category}))
        assert not (_built(others) & _declared(category))

    def test_the_default_build_serves_every_declared_tool(self) -> None:
        declared = {n for c in CATEGORY_MODULES for n in _declared(c)}
        assert _built(DEFAULT_CATEGORIES) == declared

    def test_each_category_contributes_a_tool_unless_all_its_routes_are_pending(self) -> None:
        """A category may be empty ONLY while every one of its tools waits on a feature."""
        pending_features = {feature for feature, _, _ in PENDING_ROUTES.values()}
        expected_empty: dict[str, str] = {}
        assert _categories_with_no_route_yet() == set(expected_empty)
        for feature in expected_empty.values():
            assert feature in pending_features


class TestMcpCaller:
    @pytest.mark.parametrize("tool", sorted(EXPECTED_CALLS))
    def test_the_tool_issues_its_documented_call_once(self, tool: str) -> None:
        mcp, client = build_harness()
        method, path, kwargs, payload = EXPECTED_CALLS[tool]
        call_tool(mcp, tool, kwargs)
        assert client.calls, f"{tool} registered but issued NO call"
        assert len(client.calls) == 1, f"{tool} issued {len(client.calls)} calls"
        actual_method, actual_path, actual_payload = client.calls[0]
        assert (actual_method, actual_path) == (method, path)
        assert actual_payload == payload, f"{tool} sent {actual_payload}, documented {payload}"

    def test_every_registered_tool_is_asserted_or_exempt(self) -> None:
        mcp, _ = build_harness()
        registered = {t.name for t in mcp._tool_manager.list_tools()}  # noqa: SLF001
        missing = registered - set(EXPECTED_CALLS) - set(CALLER_ASSERTION_EXEMPT)
        assert not missing, f"tools with no caller assertion: {sorted(missing)}"

    def test_no_stale_entries(self) -> None:
        mcp, _ = build_harness()
        registered = {t.name for t in mcp._tool_manager.list_tools()}  # noqa: SLF001
        assert not (set(EXPECTED_CALLS) | set(CALLER_ASSERTION_EXEMPT)) - registered
        assert not set(EXPECTED_CALLS) & set(CALLER_ASSERTION_EXEMPT)
        assert all(len(r) > 20 for r in CALLER_ASSERTION_EXEMPT.values())

    def test_the_two_accounting_rules_agree(self) -> None:
        mcp, _ = build_harness()
        registered = {t.name for t in mcp._tool_manager.list_tools()}  # noqa: SLF001
        accounted = set(EXPECTED_CALLS) | set(CALLER_ASSERTION_EXEMPT)
        assert registered - set(EXPECTED_CALLS) - set(CALLER_ASSERTION_EXEMPT) == (
            registered - accounted
        )

    def test_the_harness_registers_every_module_of_the_registry(self) -> None:
        mcp, _ = build_harness()
        registered = {t.name for t in mcp._tool_manager.list_tools()}  # noqa: SLF001
        assert registered == _built(DEFAULT_CATEGORIES)


class TestMcpGate:
    @pytest.mark.parametrize("tool", sorted(EXPECTED_CALLS))
    def test_a_closed_gate_issues_no_call_and_says_why(self, tool: str) -> None:
        mcp, client = build_harness(gate=FakeGate(closed="backend"))
        _, _, kwargs, _ = EXPECTED_CALLS[tool]
        result = call_tool(mcp, tool, kwargs)
        assert client.calls == [], f"{tool} called the backend through a closed gate"
        assert result == {"unavailable": "backend", "reason": "backend down: test"}

    def test_a_pending_approval_body_is_returned_unchanged(self) -> None:
        from tests.support.mcp_harness import PENDING_BODY

        mcp, _ = build_harness(response=dict(PENDING_BODY))
        _, _, kwargs, _ = EXPECTED_CALLS["dataworks_publish_version"]
        assert call_tool(mcp, "dataworks_publish_version", kwargs) == PENDING_BODY


class TestMcpDeployment:
    """The reachability rule applied to configuration: a category the code enables and the
    manifest omits is unreachable in production while every other guard is green."""

    def _manifests(self) -> dict[str, set[str]]:
        pattern = _re.compile(
            r"name:\s*MCP_TOOL_CATEGORIES\s*\n(?:\s*#[^\n]*\n)*\s*value:\s*\"([^\"]+)\""
        )
        found: dict[str, set[str]] = {}
        for path in sorted((_REPO / "k8s").rglob("*.yaml")):
            match = pattern.search(path.read_text())
            if match:
                found[str(path.relative_to(_REPO))] = {
                    c.strip() for c in match.group(1).split(",") if c.strip()
                }
        assert found, "no manifest under k8s/ sets MCP_TOOL_CATEGORIES; this guard checks nothing"
        return found

    def test_every_default_category_is_deployed(self) -> None:
        deployed = set.intersection(*self._manifests().values())
        missing = {c.strip() for c in DEFAULT_CATEGORIES.split(",")} - deployed
        assert not missing, f"{sorted(missing)} are defaults the deployment switches off"

    def test_every_deployed_category_is_valid(self) -> None:
        for path, categories in self._manifests().items():
            assert categories <= VALID_CATEGORIES, (path, sorted(categories - VALID_CATEGORIES))

    def test_the_mcp_manifest_is_part_of_the_kustomization(self) -> None:
        text = (_REPO / "k8s/base/kustomization.yaml").read_text()
        assert _re.search(r"^\s*-\s*mcp\.yaml\s*$", text, _re.M)


def test_pending_tools_are_not_declared_in_any_module() -> None:
    declared = {n for c in CATEGORY_MODULES for n in _declared(c)}
    assert not declared & set(pending_tools())


def test_the_010_janitors_are_scheduled_at_their_intervals() -> None:
    """Approval expiry every 5 minutes (P-08) and activity pruning daily (010 FTDD 4.2): a task
    nothing schedules never runs (control R12 survived without this)."""
    from datetime import timedelta

    schedule = {e["task"]: e["schedule"] for e in celery_app.conf.beat_schedule.values()}
    assert schedule["midataworks.system.expire_approvals"] == timedelta(minutes=5)
    assert schedule["midataworks.system.prune_agent_requests"] == timedelta(days=1)
