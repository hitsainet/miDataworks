"""A rubric's behaviour key (015 FR-015.7 - FR-015.9, C4, D37, D58; FTDD 015 section 4.2).

Fixtures differ only in the field under test: two rubrics with one body and two keys, two rubrics
that differ only in key for the filter.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from src.core.database import sync_session_factory
from src.main import fastapi_app as app
from src.models.rubric import Rubric
from tests.integration.labeling.test_judge_and_rederive import RUBRIC, judge_body, judge_setup
from tests.support.labeling_fixtures import Labeling, set_operator


async def _create(client: httpx.AsyncClient, name: str, **extra: Any) -> dict[str, Any]:
    res = await client.post("/api/v1/rubrics", json={"name": name, "body": RUBRIC, **extra})
    assert res.status_code == 201, res.text
    return dict(res.json())


async def test_the_key_is_not_part_of_the_hash(client: httpx.AsyncClient) -> None:
    """Same name, version and body under two keys hash the same (the key classifies the text)."""
    set_operator()
    keyed = await _create(client, "bk/hash", behaviour_key="pii")
    from src.services.decision_template_service import library_hash

    assert keyed["content_hash"] == library_hash("bk/hash", 1, keyed["body"])
    assert keyed["behaviour_key"] == "pii"


async def test_a_bad_key_is_refused_naming_the_rule(client: httpx.AsyncClient) -> None:
    set_operator()
    for bad in ("Pii", "-x", "x", "a" * 65, "pii-"):
        res = await client.post(
            "/api/v1/rubrics", json={"name": "bk/bad", "body": RUBRIC, "behaviour_key": bad}
        )
        assert res.status_code == 422, bad
    assert (await _create(client, "bk/ok", behaviour_key="a" * 64))["behaviour_key"] == "a" * 64


async def test_the_patch_changes_only_the_key_of_an_unused_rubric(
    client: httpx.AsyncClient,
) -> None:
    set_operator()
    row = await _create(client, "bk/patch")
    res = await client.patch(f"/api/v1/rubrics/{row['id']}", json={"behaviour_key": "pii"})
    assert res.status_code == 200, res.text
    after = res.json()
    assert after["behaviour_key"] == "pii"
    assert after["content_hash"] == row["content_hash"] and after["body"] == row["body"]
    cleared = await client.patch(f"/api/v1/rubrics/{row['id']}", json={"behaviour_key": None})
    assert cleared.json()["behaviour_key"] is None
    # Key-only: a body in the PATCH is refused, never applied.
    refused = await client.patch(
        f"/api/v1/rubrics/{row['id']}", json={"behaviour_key": "pii", "body": RUBRIC}
    )
    assert refused.status_code == 422


async def test_a_used_rubrics_key_is_frozen(client: httpx.AsyncClient, labeling: Labeling) -> None:
    """D58: once a label run used the rubric, the PATCH is 409 TEMPLATE_IMMUTABLE."""
    version_id, rubric_id = await judge_setup(client, labeling, 3)
    run = await client.post("/api/v1/label-runs", json=judge_body(version_id, rubric_id))
    assert run.status_code == 201, run.text
    res = await client.patch(f"/api/v1/rubrics/{rubric_id}", json={"behaviour_key": "pii"})
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "TEMPLATE_IMMUTABLE"
    with sync_session_factory()() as db:
        assert db.get(Rubric, rubric_id).behaviour_key is None  # type: ignore[union-attr]


async def test_clone_keeps_or_replaces_the_key(client: httpx.AsyncClient) -> None:
    set_operator()
    row = await _create(client, "bk/clone", behaviour_key="pii")
    kept = (await client.post(f"/api/v1/rubrics/{row['id']}/clone", json={})).json()
    assert kept["behaviour_key"] == "pii" and kept["version"] == 2
    moved = (
        await client.post(
            f"/api/v1/rubrics/{row['id']}/clone", json={"behaviour_key": "high-stakes"}
        )
    ).json()
    assert moved["behaviour_key"] == "high-stakes" and moved["version"] == 3
    cleared = (
        await client.post(f"/api/v1/rubrics/{row['id']}/clone", json={"behaviour_key": None})
    ).json()
    assert cleared["behaviour_key"] is None


async def test_export_and_import_round_trip_the_key(client: httpx.AsyncClient) -> None:
    set_operator()
    row = await _create(client, "bk/rt", behaviour_key="pii")
    exported = (await client.get(f"/api/v1/rubrics/{row['id']}/export")).json()
    assert exported["behaviour"] == "pii" and "behaviour_key" not in exported
    again = (await client.post("/api/v1/rubrics/import", json=exported)).json()
    assert again["id"] == row["id"]
    # A new name imported with a key stores it.
    fresh = {**exported, "name": "bk/rt2"}
    stored = (await client.post("/api/v1/rubrics/import", json=fresh)).json()
    assert stored["behaviour_key"] == "pii" and stored["id"] != row["id"]
    # An older document without the key states nothing: the stored row keeps its key.
    older = {k: v for k, v in exported.items() if k != "behaviour"}
    kept = (await client.post("/api/v1/rubrics/import", json=older)).json()
    assert kept["id"] == row["id"] and kept["behaviour_key"] == "pii"


async def test_an_import_with_a_stored_hash_and_another_key_is_refused(
    client: httpx.AsyncClient,
) -> None:
    set_operator()
    row = await _create(client, "bk/conflict", behaviour_key="pii")
    exported = (await client.get(f"/api/v1/rubrics/{row['id']}/export")).json()
    res = await client.post("/api/v1/rubrics/import", json={**exported, "behaviour": "high-stakes"})
    assert res.status_code == 409
    error = res.json()["error"]
    assert error["code"] == "RUBRIC_BEHAVIOUR_CONFLICT"
    assert "pii" in error["message"] and "high-stakes" in error["message"]


async def test_the_list_filter_selects_by_key(client: httpx.AsyncClient) -> None:
    set_operator()
    a = await _create(client, "bk/filter-a", behaviour_key="filter-one")
    b = await _create(client, "bk/filter-b", behaviour_key="filter-two")
    one = (await client.get("/api/v1/rubrics", params={"behaviour_key": "filter-one"})).json()
    two = (await client.get("/api/v1/rubrics", params={"behaviour_key": "filter-two"})).json()
    assert [r["id"] for r in one] == [a["id"]]
    assert [r["id"] for r in two] == [b["id"]]
    every = {r["id"] for r in (await client.get("/api/v1/rubrics")).json()}
    assert {a["id"], b["id"]} <= every


def test_the_patch_route_is_live() -> None:
    """Reachability from the live app, not the module (ADR-021)."""
    paths = app.openapi()["paths"]
    op = paths["/api/v1/rubrics/{rubric_id}"]["patch"]
    schema_ref = op["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    schema = app.openapi()["components"]["schemas"][schema_ref.rsplit("/", 1)[-1]]
    assert set(schema["properties"]) == {"behaviour_key"}
    assert "behaviour_key" in json.dumps(paths["/api/v1/rubrics"]["get"]["parameters"])


async def test_the_reward_bundle_rubric_document_is_the_export(client: httpx.AsyncClient) -> None:
    """FTID 015 I1: one serialiser; a keyless rubric's document keeps its pre-015 keys."""
    from src.core.canonical_json import canonical_json
    from src.services.decision_template_service import export_rubric_document

    set_operator()
    row = await _create(client, "bk/bundle")
    with sync_session_factory()() as db:
        rubric = db.get(Rubric, row["id"])
        assert rubric is not None
        doc = export_rubric_document(rubric)
    pre_015 = {
        "format": "midataworks.rubric/v1",
        "name": rubric.name,
        "version": rubric.version,
        "body": rubric.body,
    }
    assert canonical_json(doc) == canonical_json(pre_015)
