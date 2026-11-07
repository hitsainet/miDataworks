"""The smaller 008 routes, and every 008 route reaching its service with the payload exactly once
(008 FTASKS 3.5, 9.2, 9.8, 12.2; reachability caller shape)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from tests.unit.test_reachability import EXPECTED_ROUTES_008, live_routes

AGENT = {"X-Dataworks-Agent": "agent:dataworks-mcp"}


async def test_the_licence_table_route_serves_v1(client: httpx.AsyncClient) -> None:
    body = (await client.get("/api/v1/licence-table")).json()
    assert body["version"] == 1 and "cc-by-2.0" in body["permits_redistribution"]
    assert body["classes"] == ["permits_redistribution", "private_only", "forbids_redistribution"]


async def test_terms_notes_are_operator_only_and_append_only(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    model = "autotrust/JEV-9B"
    note = {
        "training_on_outputs": "permits",
        "text": "Apache-2.0 on the card",
        "prefill_source": "hub card license: apache-2.0",
    }
    refused = await client.post(f"/api/v1/model-terms/{model}/notes", json=note, headers=AGENT)
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "agent_not_allowed"
    assert (await client.get(f"/api/v1/model-terms/{model}")).json()["notes"] == []
    first = await client.post(f"/api/v1/model-terms/{model}/notes", json=note)
    assert first.status_code == 201 and first.json()["noted_by"] == operator_name
    assert first.json()["prefill_source"] == "hub card license: apache-2.0"
    second = await client.post(
        f"/api/v1/model-terms/{model}/notes",
        json={"training_on_outputs": "forbids", "text": "new terms"},
    )
    assert second.status_code == 201
    terms = (await client.get(f"/api/v1/model-terms/{model}")).json()
    assert terms["latest"] == "forbids" and len(terms["notes"]) == 2, "append-only: both kept"
    assert (await client.put(f"/api/v1/model-terms/{model}/notes", json=note)).status_code == 405


async def test_config_versions_are_numbered_hashed_and_canonical(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    a = {
        "kind": "grader",
        "name": "exact-answer",
        "body": {
            "plugin": "reference_match",
            "params": {"reference_version_id": "v1", "match": "exact"},
        },
    }
    first = (await client.post("/api/v1/config-versions", json=a)).json()
    reordered = {
        **a,
        "body": {
            "params": {"match": "exact", "reference_version_id": "v1"},
            "plugin": "reference_match",
        },
    }
    second = (await client.post("/api/v1/config-versions", json=reordered)).json()
    assert first["number"] == 1 and second["number"] == 2
    assert first["body_sha256"] == second["body_sha256"], "key order does not change the hash"
    assert first["body_format"] == "dw.grader-config/v1"
    changed = {
        **a,
        "body": {
            "plugin": "reference_match",
            "params": {"reference_version_id": "v1", "match": "fuzzy"},
        },
    }
    third = (await client.post("/api/v1/config-versions", json=changed)).json()
    assert third["body_sha256"] != first["body_sha256"]
    bad = await client.post(
        "/api/v1/config-versions", json={**a, "body": {"plugin": "judge", "params": {}}}
    )
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "config_body_invalid"
    listed = (await client.get("/api/v1/config-versions?kind=grader")).json()
    assert [c["number"] for c in listed["items"]] == [1, 2, 3]
    assert (await client.get(f"/api/v1/config-versions/{first['id']}")).json()[
        "plugin"
    ] == "reference_match"


# --- caller shape ------------------------------------------------------------------------------

V = "11111111-1111-1111-1111-111111111111"


class Stop(Exception):
    pass


def _raise(*a: Any, **k: Any) -> Any:
    raise Stop()


#: (method, template, concrete path, body, module path, attribute, check(call) -> bool)
CASES: list[tuple[str, str, str, Any, str, str, Any]] = [
    (
        "POST",
        "/api/v1/versions/{version_id}/publish-builds",
        f"/api/v1/versions/{V}/publish-builds",
        {"label_column": "label"},
        "src.services.publishing.build_service",
        "request_build",
        lambda c: c.args[1:] == (V, "label"),
    ),
    (
        "POST",
        "/api/v1/versions/{version_id}/publish-checks",
        f"/api/v1/versions/{V}/publish-checks",
        {"build_id": "pbld_1", "repo_id": "a/b"},
        "src.services.publishing.build_service",
        "version_or_refuse",
        lambda c: c.args[1] == V,
    ),
    (
        "GET",
        "/api/v1/versions/{version_id}/card-draft",
        f"/api/v1/versions/{V}/card-draft?repo_id=a/b",
        None,
        "src.services.publishing.drafts",
        "card_draft",
        lambda c: c.args[1:] == (V, "a/b", None),
    ),
    (
        "GET",
        "/api/v1/versions/{version_id}/handoff-manifest",
        f"/api/v1/versions/{V}/handoff-manifest",
        None,
        "src.services.publishing.drafts",
        "handoff_manifest_bytes",
        lambda c: c.args[1] == V,
    ),
    (
        "POST",
        "/api/v1/publishes",
        "/api/v1/publishes",
        {"version_id": V, "build_id": "pbld_1", "repo_id": "a/b"},
        "src.services.publishing.publish_service",
        "request_publish",
        lambda c: c.args[1].repo_id == "a/b"
        and c.args[1].visibility == "private"
        and type(c.args[2]).__name__ == "OperatorOrigin",
    ),
    (
        "POST",
        "/api/v1/publishes/{publish_id}/card",
        "/api/v1/publishes/pub_1/card",
        {"card_prose": "x"},
        "src.services.publishing.publish_service",
        "card_republish_request",
        lambda c: c.args[1:] == ("pub_1", "x"),
    ),
    (
        "POST",
        "/api/v1/publishes/{publish_id}/reverify",
        "/api/v1/publishes/pub_1/reverify",
        None,
        "src.services.publishing.publish_service",
        "get_publish",
        lambda c: c.args[1] == "pub_1",
    ),
    (
        "POST",
        "/api/v1/exports",
        "/api/v1/exports",
        {"target": "trl", "version_id": V, "trl_type": "dpo"},
        "src.services.exports.export_service",
        "request_export",
        lambda c: c.args[1]["trl_type"] == "dpo",
    ),
    (
        "GET",
        "/api/v1/exports/{export_id}/files/{name}",
        "/api/v1/exports/exp_1/files/train.parquet",
        None,
        "src.services.exports.export_service",
        "export_file",
        lambda c: c.args[1:] == ("exp_1", "train.parquet"),
    ),
    (
        "POST",
        "/api/v1/config-versions",
        "/api/v1/config-versions",
        {
            "kind": "selector",
            "name": "short",
            "body": {"plugin": "rule", "params": {"max_length": 5}},
        },
        "src.services.exports.config_versions",
        "create",
        lambda c: c.kwargs["name"] == "short",
    ),
    (
        "GET",
        "/api/v1/config-versions",
        "/api/v1/config-versions?kind=selector",
        None,
        "src.services.exports.config_versions",
        "list_versions",
        lambda c: c.args[1] == "selector",
    ),
    (
        "GET",
        "/api/v1/config-versions/{config_id}",
        "/api/v1/config-versions/cfg_1",
        None,
        "src.services.exports.config_versions",
        "get",
        lambda c: c.args[1] == "cfg_1",
    ),
]

#: Routes read straight from the database in the route itself, covered by behaviour tests above
#: and in test_publish_fake_hub.py / test_publish_builds_and_checks.py.
DIRECT = {
    ("GET", "/api/v1/publish-builds/{build_id}"),
    ("GET", "/api/v1/publish-check-runs/{check_run_id}"),
    ("GET", "/api/v1/publishes"),
    ("GET", "/api/v1/publishes/{publish_id}"),
    ("GET", "/api/v1/licence-table"),
    ("GET", "/api/v1/exports"),
    ("GET", "/api/v1/exports/{export_id}"),
    ("GET", "/api/v1/model-terms/{model_id}"),
    ("POST", "/api/v1/model-terms/{model_id}/notes"),
}


def test_every_008_route_is_live_and_accounted_for_here() -> None:
    assert EXPECTED_ROUTES_008 <= live_routes()
    assert {(c[0], c[1]) for c in CASES} | DIRECT == set(EXPECTED_ROUTES_008)


@pytest.mark.parametrize("case", CASES, ids=[f"{c[0]} {c[1]}" for c in CASES])
async def test_each_route_calls_its_service_once_with_the_payload(
    client: httpx.AsyncClient, operator_name: str, monkeypatch: pytest.MonkeyPatch, case: Any
) -> None:
    import importlib

    method, _, path, body, module, attr, check = case
    spy = MagicMock(side_effect=Stop())
    monkeypatch.setattr(importlib.import_module(module), attr, spy)
    await client.request(method, path, json=body)
    assert spy.call_count == 1, f"{attr} called {spy.call_count} times"
    assert check(spy.call_args), spy.call_args
