"""Revoked hashes (operator decisions ENH-006 D60, D61; 018 Review R2 F5, R2-Q5, Q3).

D60: ANY save of a revoked hash is refused - the operator's own save, a case-only edit, an agent's
save (nothing queued) - with ``409 code_rule_revoked``, before the runner is asked. Un-revoking has
one path: "approve again", the operator's alone, with a reason and the Settings PIN.

D61: "approve again" first re-runs the hash's STORED cases under the current contract floor through
the runner (``save_check``, twice on fresh instances; payload and call count asserted) and is
refused, writing nothing, when a case fails now (``409 code_rule_case_failed`` naming the case) or
the runner cannot answer (``503 runner_unavailable``).
"""

from __future__ import annotations

import copy

import httpx
import pytest

from src.runner_contract.hashing import code_sha256
from tests.support import code_rule_fixtures as fx
from tests.support.fake_runner import FakeRunner

from .conftest import AGENT, CR, count, live_tools, save

pytestmark = pytest.mark.usefixtures("operator_name")


async def _revoked(client: httpx.AsyncClient, behaviour: str) -> str:
    body = fx.save_body(behaviour)
    assert (await save(client, body)).status_code == 201
    sha = code_sha256(body["document"])
    revoked = await client.post(f"{CR}/approvals/{sha}/revoke", json={"reason": "reads too much"})
    assert revoked.status_code == 200, revoked.text
    return sha


# --- D60 ------------------------------------------------------------------------------------------


async def test_the_operators_own_save_of_a_revoked_hash_is_refused(
    client: httpx.AsyncClient, fake: FakeRunner, behaviour: str
) -> None:
    sha = await _revoked(client, behaviour)
    rows, decisions, asked = (
        count("dw_config_versions"),
        count("dw_code_rule_approvals"),
        len(fake.requests),
    )
    refused = await save(client, fx.save_body(behaviour))
    assert refused.status_code == 409, refused.text
    error = refused.json()["error"]
    assert error["code"] == "code_rule_revoked"
    assert error["details"]["code_sha256"] == sha and error["details"]["state"] == "revoked"
    assert error["details"]["next"]["tool"] in await live_tools()
    assert count("dw_config_versions") == rows and count("dw_code_rule_approvals") == decisions
    assert len(fake.requests) == asked, "refused before the runner was asked"


async def test_a_case_only_edit_of_a_revoked_hash_is_refused_too(
    client: httpx.AsyncClient, fake: FakeRunner, behaviour: str
) -> None:
    await _revoked(client, behaviour)
    edited = fx.save_body(behaviour)
    edited["cases"] = copy.deepcopy(edited["cases"])
    edited["cases"][1]["id"] = "quiet-renamed"
    refused = await save(client, edited)
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "code_rule_revoked"


async def test_an_agents_save_of_a_revoked_hash_is_refused_and_queues_nothing(
    client: httpx.AsyncClient, fake: FakeRunner, behaviour: str
) -> None:
    await _revoked(client, behaviour)
    refused = await save(client, fx.save_body(behaviour), agent=True)
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "code_rule_revoked"
    assert count("dw_approvals") == 0, "no card the generic approve could undo it from"


async def test_approve_again_is_the_one_way_back_and_then_saves_work(
    client: httpx.AsyncClient, fake: FakeRunner, behaviour: str
) -> None:
    sha = await _revoked(client, behaviour)
    agent = await client.post(f"{CR}/approvals/{sha}/approve", json={"reason": "x"}, headers=AGENT)
    assert agent.status_code == 403
    again = await client.post(f"{CR}/approvals/{sha}/approve", json={"reason": "narrowed"})
    assert again.status_code == 200, again.text
    assert again.json()["state"] == "approved" and again.json()["changed"] is True
    assert (await save(client, fx.save_body(behaviour))).status_code == 201


async def test_approve_again_needs_the_settings_pin_when_one_is_set(
    client: httpx.AsyncClient, fake: FakeRunner
) -> None:
    from tests.integration.test_settings_pin import HEADER, _set_pin

    sha = await _revoked(client, "fixture-gamma")
    token = await _set_pin(client)
    refused = await client.post(f"{CR}/approvals/{sha}/approve", json={"reason": "r"})
    assert refused.status_code in (401, 403), refused.text
    assert count("dw_code_rule_approvals", "code_sha256 = :s", s=sha) == 2
    ok = await client.post(
        f"{CR}/approvals/{sha}/approve", json={"reason": "r"}, headers={HEADER: token}
    )
    assert ok.status_code == 200, ok.text


# --- D61 ------------------------------------------------------------------------------------------


async def test_approve_again_reruns_the_stored_cases_at_the_floor_through_the_runner(
    client: httpx.AsyncClient, fake: FakeRunner, behaviour: str
) -> None:
    from src.services.code_rules import limits

    sha = await _revoked(client, behaviour)
    before = len(fake.runs("save_check"))
    again = await client.post(f"{CR}/approvals/{sha}/approve", json={"reason": "narrowed"})
    assert again.status_code == 200, again.text
    runs = fake.runs("save_check")[before:]
    assert len(runs) == 1, "one request: every stored case, twice"
    (run,) = runs
    cases = fx.fixture(behaviour)["cases"]
    assert run["document"]["code_sha256"] == sha
    assert run["limits"] == limits.floor() and run["lane"] == "interactive"
    assert [(c["function"], c["texts"], c["repeat"]) for c in run["calls"]] == [
        (c["function"], [c["text"]], 2) for c in cases
    ]
    assert [r["id"] for r in again.json()["case_results"]] == [c["id"] for c in cases]


async def test_approve_again_is_refused_when_a_stored_case_fails_now(
    client: httpx.AsyncClient, fake: FakeRunner, behaviour: str
) -> None:
    sha = await _revoked(client, behaviour)
    function = fx.fixture(behaviour)["cases"][0]["function"]
    fake.twins[sha] = {function: lambda text, params: 0}  # the stored "fires" case fails now
    refused = await client.post(f"{CR}/approvals/{sha}/approve", json={"reason": "narrowed"})
    assert refused.status_code == 409, refused.text
    error = refused.json()["error"]
    assert error["code"] == "code_rule_case_failed" and error["details"]["case"] == "fires"
    assert error["details"]["next"]["tool"] in await live_tools()
    state = (await client.get(f"{CR}/approvals", params={"code_sha256": sha})).json()["state"]
    assert state == "revoked"
    assert count("dw_code_rule_approvals", "code_sha256 = :s", s=sha) == 2


async def test_approve_again_with_the_runner_down_is_503_and_writes_nothing(
    client: httpx.AsyncClient, fake: FakeRunner, behaviour: str
) -> None:
    sha = await _revoked(client, behaviour)
    fake.runs_down = True
    refused = await client.post(f"{CR}/approvals/{sha}/approve", json={"reason": "narrowed"})
    assert refused.status_code == 503, refused.text
    assert refused.json()["error"]["code"] == "runner_unavailable"
    assert count("dw_code_rule_approvals", "code_sha256 = :s", s=sha) == 2
