"""List, get, by hash, archive (FR-018.33, FR-018.34; FTASKS 10.8, 10.9)."""

from __future__ import annotations

import copy
import json

import httpx
import pytest

from src.runner_contract.hashing import canonical_document_bytes, code_sha256
from tests.support import code_rule_fixtures as fx
from tests.support.fake_runner import FakeRunner

from .conftest import AGENT, CR, live_tools, save

pytestmark = pytest.mark.usefixtures("operator_name")


async def _both(client: httpx.AsyncClient) -> None:
    for behaviour in ("fixture-gamma", "fixture-delta"):
        assert (await save(client, fx.save_body(behaviour))).status_code == 201


async def test_the_behaviour_filter_separates_two_rules(
    client: httpx.AsyncClient, fake: FakeRunner
) -> None:
    await _both(client)
    gamma = (await client.get(CR, params={"behaviour_key": "fixture-gamma"})).json()
    assert [i["name"] for i in gamma["items"]] == ["gamma-digit-sum"]
    item = gamma["items"][0]
    assert item["code_sha256"] == code_sha256(fx.fixture("fixture-gamma")["document"])
    assert item["approval_state"] == "approved" and item["functions"] == ["digit_sum"]
    assert item["last_case_result"]["passed"] is True
    assert item["last_case_result"]["contract_version"] == "1.0"
    assert gamma["next"]["tool"] in await live_tools()


async def test_pages_are_disjoint_and_archive_hides(
    client: httpx.AsyncClient, fake: FakeRunner
) -> None:
    await _both(client)
    first = (await client.get(CR, params={"page": 1, "limit": 1})).json()
    second = (await client.get(CR, params={"page": 2, "limit": 1})).json()
    assert first["total"] == second["total"] == 2
    assert {i["name"] for i in first["items"]}.isdisjoint({i["name"] for i in second["items"]})
    archived = await client.post(f"{CR}/delta-loud-words/archive", json={"reason": "done"})
    assert archived.status_code == 200 and archived.json()["archived"] is True
    assert archived.json()["next"]["tool"] in await live_tools()
    assert [i["name"] for i in (await client.get(CR)).json()["items"]] == ["gamma-digit-sum"]
    shown = (await client.get(CR, params={"include_archived": True})).json()["items"]
    assert [i["archived"] for i in shown] == [True, False]
    back = await client.post(f"{CR}/delta-loud-words/unarchive", json={}, headers=AGENT)
    assert back.status_code == 200 and back.json()["archived"] is False


async def test_get_one_version_with_superseded_by_and_honest_use_records(
    client: httpx.AsyncClient, fake: FakeRunner
) -> None:
    body = fx.save_body("fixture-gamma")
    await save(client, body)
    edited = copy.deepcopy(body)
    edited["cases"][1]["id"] = "quiet-renamed"
    await save(client, edited)
    one = (await client.get(f"{CR}/gamma-digit-sum/versions/1")).json()
    assert one["superseded_by"] == 2
    assert one["document"] == body["document"] and one["cases"][1]["id"] == "quiet"
    assert one["approval"]["state"] == "approved" and one["approval"]["history_rows"] == 1
    assert one["contract_status"]["passed"] is True
    # Slice 4: builds record what they ran; no build ran it yet (test_use_records.py has one).
    assert one["used_by"] == {"recorded": True, "versions": []}
    assert one["next"]["tool"] in await live_tools()
    missing = await client.get(f"{CR}/gamma-digit-sum/versions/9")
    assert missing.status_code == 404
    assert missing.json()["error"]["details"]["next"]["tool"] in await live_tools()


async def test_by_hash_returns_the_exact_hashed_text(
    client: httpx.AsyncClient, fake: FakeRunner
) -> None:
    body = fx.save_body("fixture-delta")
    await save(client, body)
    sha = code_sha256(body["document"])
    out = (await client.get(f"{CR}/by-hash/{sha}")).json()
    assert out["canonical_utf8"] == canonical_document_bytes(body["document"]).decode()
    assert code_sha256(json.loads(out["canonical_utf8"])) == out["sha256"] == sha
    assert out["document"] == body["document"] and out["cases"] == body["cases"]
    assert out["approval_state"] == "approved"
    assert (await client.get(f"{CR}/by-hash/{'b' * 64}")).status_code == 404


async def test_the_runner_contract_is_the_files_values_and_the_live_settings(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.core.config import get_settings
    from src.runner_contract.document import PACKAGED_CONTRACT_PATH

    published = json.loads(PACKAGED_CONTRACT_PATH.read_text(encoding="utf-8"))
    out = (await client.get("/api/v1/runner/contract")).json()
    for key in ("format", "contract_version", "language", "stdlib_allowed", "limits_floor"):
        assert out[key] == published[key], key
    assert (out["count_cap"], out["reasons"], out["diagnostic"]) == (
        published["count_cap"],
        published["reasons"],
        published["diagnostic"],
    )
    assert out["limits_in_force"]["call_fuel"] == published["limits_floor"]["call_fuel"]
    monkeypatch.setattr(
        get_settings(), "code_rule_call_fuel", published["limits_floor"]["call_fuel"] * 2
    )
    monkeypatch.setattr(get_settings(), "code_rule_dry_run_per_minute", 7)
    changed = (await client.get("/api/v1/runner/contract")).json()
    assert changed["limits_in_force"]["call_fuel"] == published["limits_floor"]["call_fuel"] * 2
    assert changed["dry_run"]["per_minute"] == 7
    assert changed["next"]["tool"] in await live_tools()
