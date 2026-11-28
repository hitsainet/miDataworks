"""Drafts (task 5.2): invalid drafts saved, invalid save refused, flow state round-trip, who."""

from __future__ import annotations

import httpx
import pytest

from tests.support.stub_operators import StubRegistry, body, install_stubs

D = "/api/v1/recipe-drafts"


@pytest.fixture
def stubs(monkeypatch: pytest.MonkeyPatch) -> StubRegistry:
    return install_stubs(monkeypatch)


async def test_an_invalid_draft_is_saved_and_refused_on_save(
    client: httpx.AsyncClient, stubs: StubRegistry, operator_name: str
) -> None:
    bad = body(("stub_drop_short", {"min_len": "abc"}))
    created = await client.post(D, json={"name": "wip", "body": bad})
    assert created.status_code == 201, created.text
    assert created.json()["updated_by"] == operator_name
    saved = await client.post(f"{D}/{created.json()['id']}/save", json={})
    assert saved.status_code == 422
    error = saved.json()["error"]
    assert error["code"] == "recipe_invalid"
    assert error["details"]["steps"][0]["errors"][0]["code"] == "params_invalid"


async def test_flow_state_round_trips_and_put_updates(
    client: httpx.AsyncClient, stubs: StubRegistry, operator_name: str
) -> None:
    state = {"step": "curate", "choices": {"goal": "detector", "band": [0.3, 0.7]}}
    created = (await client.post(D, json={"flow_state": state})).json()
    assert (await client.get(f"{D}/{created['id']}")).json()["flow_state"] == state
    updated = await client.put(
        f"{D}/{created['id']}",
        json={"flow_state": {"step": "label", "choices": {}}, "body": {"format": "x"}},
        headers={"X-Dataworks-Agent": "agent:mcp"},
    )
    assert updated.status_code == 200
    assert updated.json()["flow_state"]["step"] == "label"
    assert updated.json()["updated_by"] == "agent:mcp"
    assert updated.json()["updated_by_origin"] == "agent"


async def test_save_creates_a_recipe_then_revises_it(
    client: httpx.AsyncClient, stubs: StubRegistry, operator_name: str
) -> None:
    created = (await client.post(D, json={"body": body(("stub_keep", {}))})).json()
    unnamed = await client.post(f"{D}/{created['id']}/save", json={})
    assert unnamed.json()["error"]["code"] == "recipe_name_required"
    first = await client.post(f"{D}/{created['id']}/save", json={"recipe_name": "from-draft"})
    assert first.status_code == 201 and first.json()["revision_number"] == 1
    draft = (await client.get(f"{D}/{created['id']}")).json()
    assert draft["recipe_id"] == first.json()["recipe_id"]
    await client.put(
        f"{D}/{created['id']}",
        json={"recipe_id": draft["recipe_id"], "body": body(("stub_drop_short", {"min_len": 4}))},
    )
    second = await client.post(f"{D}/{created['id']}/save", json={})
    assert second.status_code == 201 and second.json()["revision_number"] == 2


async def test_delete_and_not_found(
    client: httpx.AsyncClient, stubs: StubRegistry, operator_name: str
) -> None:
    created = (await client.post(D, json={})).json()
    assert (await client.delete(f"{D}/{created['id']}")).status_code == 204
    missing = await client.get(f"{D}/{created['id']}")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "draft_not_found"
    assert (await client.get(D)).json()["total"] == 0
