"""Every gated route is known, and each has a test proving an agent call waits (FR-010.27; 9.4).

``GATED_ROUTES`` is the expected set; it must EQUAL the routes the live OpenAPI document marks with
``x-approval-action`` (so a new gated route, or a lost decorator, fails here). Each entry names the
integration test that sends an agent call to that route and asserts 202 with nothing executed;
those tests live beside their feature because each needs its feature's fixtures. This file checks
the test exists by AST, so deleting it turns this red.

A route not yet served (006's targets, 009's sends) joins this map in the commit
that serves it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.core.agent_origin import APPROVAL_ACTIONS
from src.main import fastapi_app

TESTS = Path(__file__).resolve().parents[1]

#: (method, template) -> (action, test file relative to tests/, test function)
GATED_ROUTES: dict[tuple[str, str], tuple[str, str, str]] = {
    ("PUT", "/api/v1/settings/{key}"): (
        "secret_write",
        "integration/test_approvals.py",
        "test_an_approved_secret_write_stores_the_real_secret",
    ),
    ("DELETE", "/api/v1/settings/{key}"): (
        "secret_write",
        "integration/test_approval_coverage.py",
        "test_an_agent_secret_delete_waits",
    ),
    ("PUT", "/api/v1/endpoint-roles/{role}"): (
        "secret_write",
        "integration/test_approval_coverage.py",
        "test_an_agent_endpoint_key_write_waits",
    ),
    ("POST", "/api/v1/sources/hf/preview"): (
        "secret_write",
        "integration/test_secret_never_written.py",
        "test_hf_tokens_appear_nowhere_after_preview_import_and_agent_import",
    ),
    ("POST", "/api/v1/sources/hf"): (
        "secret_write",
        "integration/test_agent_token_approval.py",
        "test_an_agent_token_waits_and_is_held_only_encrypted",
    ),
    ("PUT", "/api/v1/calibration-targets"): (
        "gate_target_write",
        "integration/calibration/test_agent_target_approval.py",
        "test_an_agent_write_waits_and_writes_nothing",
    ),
    ("POST", "/api/v1/reproduction-links"): (
        "gate_target_write",
        "integration/detector_sets/test_reproduction_links.py",
        "test_an_agent_link_waits_for_approval_and_records_it",
    ),
    ("POST", "/api/v1/sources/{source_id}/annotations"): (
        "source_annotate",
        "integration/test_agent_annotation_approval.py",
        "test_an_agent_annotation_of_every_kind_waits_and_writes_nothing",
    ),
    ("POST", "/api/v1/versions"): (
        "agent_label_rows",
        "integration/test_build_pipeline.py",
        "test_an_agent_build_over_the_threshold_waits_for_approval",
    ),
    ("POST", "/api/v1/recipes/{recipe_id}/build"): (
        "agent_label_rows",
        "integration/test_build_pipeline.py",
        "test_an_agent_recipe_build_over_the_threshold_waits_for_approval",
    ),
    ("POST", "/api/v1/label-runs"): (
        "agent_label_rows",
        "integration/labeling/test_approval_gate.py",
        "test_agent_5001_rows_waits_and_5000_starts",
    ),
    ("DELETE", "/api/v1/versions/{version_id}"): (
        "version_delete",
        "integration/test_version_delete.py",
        "test_an_agent_delete_waits_for_approval_and_runs_once",
    ),
    ("POST", "/api/v1/publishes"): (
        "hub_push",
        "integration/publishing/test_publish_approvals.py",
        "test_an_agent_publish_waits_and_nothing_reaches_the_hub",
    ),
    ("POST", "/api/v1/publishes/{publish_id}/card"): (
        "hub_push",
        "integration/publishing/test_publish_approvals.py",
        "test_the_card_route_is_gated_too",
    ),
    ("POST", "/api/v1/detector-sets/{set_id}/send"): (
        "hub_push",
        "integration/detector_sets/test_send_resume_and_approval.py",
        "test_an_agent_send_waits_for_approval_and_runs_once",
    ),
}


#: Routes that carry a SECOND action, chosen per request (``x-approval-actions``; feature 012:
#: the settings routes gate a secret as secret_write and a runtime setting as config_write).
#: (method, template) -> (action, test file relative to tests/, test function)
SECOND_ACTIONS: dict[tuple[str, str], tuple[str, str, str]] = {
    ("PUT", "/api/v1/settings/{key}"): (
        "config_write",
        "integration/test_runtime_settings_api.py",
        "test_an_agent_change_waits_and_runs_once_on_approve",
    ),
    ("DELETE", "/api/v1/settings/{key}"): (
        "config_write",
        "integration/test_runtime_settings_api.py",
        "test_an_agent_reset_waits_and_runs_once_on_approve",
    ),
    # Feature 014 (K2): an agent's keyless endpoint-role write waits too.
    ("PUT", "/api/v1/endpoint-roles/{role}"): (
        "config_write",
        "integration/test_settings_pin.py",
        "test_an_agent_write_waits_for_approval_and_the_approved_request_runs_without_the_pin",
    ),
}


def second_actions() -> dict[tuple[str, str], list[str]]:
    out = {}
    for path, operations in fastapi_app.openapi()["paths"].items():
        for method, operation in operations.items():
            extra = [
                a
                for a in operation.get("x-approval-actions", [])
                if a != operation.get("x-approval-action")
            ]
            if extra:
                out[(method.upper(), path)] = extra
    return out


def test_the_second_actions_equal_the_expected_ones() -> None:
    expected = {key: [action] for key, (action, _, _) in SECOND_ACTIONS.items()}
    assert second_actions() == expected


@pytest.mark.parametrize("route", sorted(SECOND_ACTIONS))
def test_each_second_action_has_its_agent_test(route: tuple[str, str]) -> None:
    _, rel, name = SECOND_ACTIONS[route]
    tree = ast.parse((TESTS / rel).read_text())
    names = {
        n.name for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef)
    }
    assert name in names, f"{rel} has no {name}; the agent gate on {route} is untested"


def marked_routes() -> dict[tuple[str, str], str]:
    out = {}
    for path, operations in fastapi_app.openapi()["paths"].items():
        for method, operation in operations.items():
            action = operation.get("x-approval-action")
            if action:
                out[(method.upper(), path)] = action
    return out


def test_the_marked_routes_equal_the_expected_gated_routes() -> None:
    expected = {key: action for key, (action, _, _) in GATED_ROUTES.items()}
    assert marked_routes() == expected


#: Registered actions with no served route yet, and why.
UNATTACHED = {
    "millm_model_load": "No route in v1 (T-37).",
}


def test_every_action_is_attached_to_a_route_or_waits_with_a_reason() -> None:
    """S3-01, S3-08: seven actions; feature 012: config_write (a second action on settings)."""
    attached = set(marked_routes().values()) | {
        a for actions in second_actions().values() for a in actions
    }
    assert set(APPROVAL_ACTIONS) - attached == set(UNATTACHED), attached


@pytest.mark.parametrize("route", sorted(GATED_ROUTES))
def test_each_gated_route_has_its_agent_test(route: tuple[str, str]) -> None:
    _, rel, name = GATED_ROUTES[route]
    tree = ast.parse((TESTS / rel).read_text())
    names = {
        n.name for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef)
    }
    assert name in names, f"{rel} has no {name}; the agent gate on {route} is untested"


AGENT = {"X-Dataworks-Agent": "agent:dataworks-mcp"}


async def test_an_agent_secret_delete_waits(client, operator_name: str) -> None:  # type: ignore[no-untyped-def]
    assert (
        await client.put("/api/v1/settings/hf_token", json={"value": "hf_abc"})
    ).status_code == 200
    response = await client.delete("/api/v1/settings/hf_token", headers=AGENT)
    assert response.status_code == 202 and response.json()["action"] == "secret_write"
    settings = (await client.get("/api/v1/settings/hf_token")).json()
    assert settings["is_set"] is True, "the delete ran before the operator approved it"


async def test_an_agent_endpoint_key_write_waits(client, operator_name: str) -> None:  # type: ignore[no-untyped-def]
    body = {"protocol": None, "base_url": None, "model_id": None, "api_key": "sk-agent"}
    response = await client.put("/api/v1/endpoint-roles/judge", json=body, headers=AGENT)
    assert response.status_code == 202 and response.json()["action"] == "secret_write"
    role = (await client.get("/api/v1/endpoint-roles/judge")).json()
    assert role["has_api_key"] is False, role
