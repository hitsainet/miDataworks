"""The gated save (FR-018.28, FR-018.29, FR-018.31, FR-018.32; FTASKS 10.1, 10.3 - 10.5).

Payload and call count throughout: what the executor replayed and how often, what the runner was
sent, which rows exist. Each refusal asserts its code, the item it names and a ``next`` naming a
LIVE tool.
"""

from __future__ import annotations

import asyncio
import copy
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from src.core.database import get_sync_engine
from src.runner_contract.hashing import code_sha256
from tests.support import code_rule_fixtures as fx
from tests.support.fake_runner import FakeRunner

from .conftest import approve, count, live_tools, save

pytestmark = pytest.mark.usefixtures("operator_name")


def _approvals(sha: str) -> list[dict[str, Any]]:
    with get_sync_engine().connect() as conn:
        rows = conn.execute(
            text(
                "SELECT action, reason, changed_by, origin, approval_id FROM "
                "dw_code_rule_approvals WHERE code_sha256 = :s ORDER BY id"
            ),
            {"s": sha},
        ).mappings()
        return [dict(r) for r in rows]


# --- the operator's save ---------------------------------------------------------------------------


async def test_the_operators_save_stores_the_version_and_is_the_approval(
    client: httpx.AsyncClient, fake: FakeRunner, behaviour: str
) -> None:
    body = fx.save_body(behaviour)
    sha = code_sha256(body["document"])
    response = await save(client, body)
    assert response.status_code == 201, response.text
    out = response.json()
    assert (out["name"], out["version"], out["code_sha256"], out["created"]) == (
        body["name"],
        1,
        sha,
        True,
    )
    assert out["approval_state"] == "approved"
    assert out["next"]["tool"] in await live_tools()
    assert _approvals(sha) == [
        {
            "action": "approve",
            "reason": "saved by the operator",
            "changed_by": "Test Operator",
            "origin": "operator",
            "approval_id": None,
        }
    ]
    # The runner was asked to inspect once and to run every case TWICE at the FLOOR (FR-018.20).
    assert [r["path"] for r in fake.requests] == ["/v1/inspect", "/v1/run"]
    (run,) = fake.runs()
    from src.services.code_rules import limits

    assert run["mode"] == "save_check" and run["lane"] == "interactive"
    assert run["limits"] == limits.floor()
    assert [c["repeat"] for c in run["calls"]] == [2] * len(body["cases"])
    assert [c["texts"] for c in run["calls"]] == [[c["text"]] for c in body["cases"]]
    with get_sync_engine().connect() as conn:
        stored = conn.execute(
            text(
                "SELECT body_sha256, body_canonical, plugin FROM dw_config_versions "
                "WHERE kind = 'code_rule'"
            )
        ).one()
    assert stored.body_sha256 == sha and stored.plugin == "code_rule"
    assert count("dw_code_rule_contract_runs", "code_sha256 = :s AND passed", s=sha) == 1


async def test_a_save_runs_the_cases_at_the_floor_even_when_this_deployment_raised_a_limit(
    client: httpx.AsyncClient, fake: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-018.32: a case must pass at the contract FLOOR, not merely this deployment's limits."""
    from src.core.config import get_settings
    from src.services.code_rules import limits

    floor = limits.floor()
    monkeypatch.setattr(get_settings(), "code_rule_call_fuel", floor["call_fuel"] * 4)
    monkeypatch.setattr(get_settings(), "code_rule_memory_bytes", floor["memory_bytes"] * 2)
    assert (await save(client, fx.save_body("fixture-delta"))).status_code == 201
    (run,) = fake.runs()
    assert run["limits"] == floor
    assert run["limits"]["call_fuel"] != limits.in_force()["call_fuel"]


async def test_two_concurrent_saves_of_one_name_take_distinct_numbers(
    client: httpx.AsyncClient, fake: FakeRunner
) -> None:
    first = fx.save_body("fixture-gamma")
    second = fx.save_body("fixture-gamma")
    second["cases"][0]["id"] = "fires-again"  # a case-only edit: same hash, a new version
    a, b = await asyncio.gather(save(client, first), save(client, second))
    assert {a.status_code, b.status_code} == {201}
    assert sorted([a.json()["version"], b.json()["version"]]) == [1, 2]


# --- an agent's save: pending, nothing written, executed once on approval ------------------------


async def test_an_agent_save_of_a_new_hash_waits_and_writes_nothing(
    client: httpx.AsyncClient, fake: FakeRunner, executor_spy: list[dict[str, Any]], behaviour: str
) -> None:
    body = fx.save_body(behaviour)
    sha = code_sha256(body["document"])
    pending = await save(client, body, agent=True)
    assert pending.status_code == 202, pending.text
    answer = pending.json()
    assert answer["status"] == "pending" and answer["action"] == "code_rule_save"
    assert answer["next"]["tool"] == "dataworks_get_approval_status"
    assert answer["next"]["tool"] in await live_tools()
    assert executor_spy == []
    assert count("dw_config_versions", "kind = 'code_rule'") == 0
    assert count("dw_code_rules") == 0
    assert _approvals(sha) == []
    with get_sync_engine().connect() as conn:
        stored = conn.execute(
            text("SELECT summary, payload FROM dw_approvals WHERE id = :i"),
            {"i": answer["approval_id"]},
        ).one()
    facts = stored.payload["facts"]
    assert facts["code_sha256"] == sha
    assert [c["id"] for c in facts["case_results"]] == [c["id"] for c in body["cases"]]
    assert all(c["passed"] for c in facts["case_results"])
    assert facts["functions"] == [f["name"] for f in body["document"]["functions"]]
    # The one-line summary names the rule and the hash, never a line of source (FR-018.25).
    assert stored.summary == f"Save code rule {body['name']} ({sha[:12]}...)"
    assert "def " not in stored.summary

    decided = await approve(client, answer["approval_id"])
    assert decided["status"] == "executed", decided
    assert len(executor_spy) == 1
    assert executor_spy[0] == {"body": body, "approval_id": answer["approval_id"]}
    assert count("dw_config_versions", "kind = 'code_rule'") == 1
    assert _approvals(sha) == [
        {
            "action": "approve",
            "reason": f"approved the agent's request {answer['approval_id']}",
            "changed_by": "Test Operator",  # the operator who decided, not the agent
            "origin": "operator",
            "approval_id": answer["approval_id"],
        }
    ]


async def test_an_approved_hash_saves_without_a_new_approval_and_a_case_edit_too(
    client: httpx.AsyncClient, fake: FakeRunner, executor_spy: list[dict[str, Any]]
) -> None:
    body = fx.save_body("fixture-gamma")
    sha = code_sha256(body["document"])
    assert (await save(client, body)).status_code == 201  # the operator approves by saving
    edited = copy.deepcopy(body)
    edited["cases"].append(
        {**edited["cases"][0], "id": "fires-more", "text": "19 28", "expect": {"count": 2}}
    )
    response = await save(client, edited, agent=True)
    assert response.status_code == 201, response.text  # M12: no gate for an approved hash
    out = response.json()
    assert (out["version"], out["code_sha256"], out["created"]) == (2, sha, True)
    assert count("dw_approvals") == 0, "a case-only edit asks for no approval (FR-018.31)"
    assert executor_spy == []
    assert len(_approvals(sha)) == 1, "no second approve row for an approved hash"
    # The same body again stores nothing.
    again = (await save(client, edited, agent=True)).json()
    assert (again["version"], again["created"]) == (2, False)


@pytest.mark.parametrize(
    "change",
    ["source_byte", "derived_from", "params_schema"],
)
async def test_any_change_to_the_hashed_document_needs_a_new_approval(
    client: httpx.AsyncClient, fake: FakeRunner, change: str
) -> None:
    body = fx.save_body("fixture-gamma")
    assert (await save(client, body)).status_code == 201
    changed = copy.deepcopy(body)
    document = changed["document"]
    if change == "source_byte":
        document["source"] = document["source"].replace("n = 0", "n = 0 ")
    elif change == "derived_from":
        document["derived_from"]["statement"] += " Again."
    else:
        document["params_schema"]["properties"]["sums"]["maxItems"] = 50
    new_sha = code_sha256(document)
    assert new_sha != code_sha256(body["document"])
    fake.twins[new_sha] = fx.fixture("fixture-gamma")["twin"]
    response = await save(client, changed, agent=True)
    assert response.status_code == 202, response.text
    assert count("dw_approvals", "action = 'code_rule_save' AND status = 'pending'") == 1


async def test_a_refusal_in_the_runner_checks_queues_nothing(
    client: httpx.AsyncClient, fake: FakeRunner
) -> None:
    body = fx.save_body("fixture-gamma")
    body["cases"][0]["expect"] = {"count": 99}
    response = await save(client, body, agent=True)
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "code_rule_case_failed"
    assert error["details"]["case"] == "fires"
    assert error["details"]["expected"] == {"count": 99}
    assert error["details"]["actual"] == {"count": 3}
    assert error["details"]["next"]["tool"] in await live_tools()
    assert count("dw_approvals") == 0 and count("dw_config_versions") == 0


async def test_a_static_refusal_never_reaches_the_runner(
    client: httpx.AsyncClient, fake: FakeRunner
) -> None:
    body = fx.save_body("fixture-gamma")
    body["document"]["functions"][0]["name"] = "Not A Name"
    response = await save(client, body, agent=True)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "code_rule_invalid"
    assert error["details"]["pointer"] == "/document/functions/0/name"
    assert fake.requests == [] and count("dw_approvals") == 0


# --- every refusal: code, item, next (FR-018.32) --------------------------------------------------


def _mutate(kind: str, body: dict[str, Any], fake: FakeRunner) -> None:
    if kind == "cases_missing":
        body["cases"] = [c for c in body["cases"] if c["expect"]["count"] > 0]
    elif kind == "function_missing":
        fake.inspect = {"functions": [], "imports": []}
    elif kind == "load_refused":
        fake.inspect = {"reason": "rule_load_refused", "refused": "socket"}
    elif kind == "nondeterministic":
        fake.vary_second = True
    elif kind == "source_cap":
        body["document"]["source"] += "#" * 70_000
    elif kind == "contract_major":
        body["document"]["runner_contract"] = "midataworks.runner/v2"
    elif kind == "case_params":
        body["cases"][0]["params"] = {"sums": "ten"}
    elif kind == "case_function":
        body["cases"][0]["function"] = "elsewhere"
    elif kind == "lone_surrogate":
        body["document"]["source"] += "# \ud800\n"


REFUSALS = {
    "cases_missing": (422, "code_rule_cases_missing", "function", "digit_sum"),
    "function_missing": (422, "code_rule_function_missing", "function", "digit_sum"),
    "load_refused": (422, "code_rule_load_refused", "refused", "socket"),
    "nondeterministic": (422, "code_rule_nondeterministic", "case", "fires"),
    "source_cap": (413, "cap_exceeded", "setting", "CODE_RULE_MAX_SOURCE_BYTES"),
    "contract_major": (422, "runner_contract_unknown", "runner_contract", "midataworks.runner/v2"),
    "case_params": (422, "code_rule_params_invalid", "pointer", "/cases/0/params/sums"),
    "case_function": (422, "code_rule_function_missing", "case", "fires"),
    "lone_surrogate": (422, "code_rule_invalid", "pointer", "/document/source"),
}


@pytest.mark.parametrize("kind", sorted(REFUSALS))
async def test_each_refusal_names_its_item_and_a_next(
    client: httpx.AsyncClient, fake: FakeRunner, kind: str
) -> None:
    body = fx.save_body("fixture-gamma")
    _mutate(kind, body, fake)
    if kind == "lone_surrogate":
        import json as _json

        response = await client.post(
            "/api/v1/code-rules",
            content=_json.dumps(body).encode(),
            headers={"content-type": "application/json"},
        )
    else:
        response = await save(client, body)
    status, code, key, value = REFUSALS[kind]
    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert error["code"] == code
    assert error["details"][key] == value
    assert error["details"]["next"]["tool"] in await live_tools()
    assert count("dw_config_versions") == 0 and count("dw_code_rule_approvals") == 0


async def test_a_name_keeps_its_behaviour_key(client: httpx.AsyncClient, fake: FakeRunner) -> None:
    assert (await save(client, fx.save_body("fixture-gamma"))).status_code == 201
    other = fx.save_body("fixture-gamma", behaviour_key="fixture-delta")
    asked = len(fake.requests)
    response = await save(client, other)
    assert response.status_code == 409
    assert len(fake.requests) == asked, "refused before the runner is asked anything"
    error = response.json()["error"]
    assert error["code"] == "code_rule_name_taken"
    assert error["details"]["behaviour_key"] == "fixture-gamma"
    assert error["details"]["next"] == {
        "tool": "dataworks_list_code_rules",
        "args": {"behaviour_key": "fixture-gamma"},
        "why": error["details"]["next"]["why"],
    }


async def test_two_concurrent_first_saves_of_one_name_under_two_keys_keep_one_key(
    client: httpx.AsyncClient, fake: FakeRunner
) -> None:
    """The early check sees no head for either; the head-row lock decides (one 201, one 409)."""
    gamma = fx.save_body("fixture-gamma")
    delta = fx.save_body("fixture-gamma", behaviour_key="fixture-delta")
    a, b = await asyncio.gather(save(client, gamma), save(client, delta))
    assert sorted([a.status_code, b.status_code]) == [201, 409]
    loser = a if a.status_code == 409 else b
    assert loser.json()["error"]["code"] == "code_rule_name_taken"
    assert count("dw_config_versions", "kind = 'code_rule'") == 1


async def test_switched_off_and_runner_down_are_named(
    client: httpx.AsyncClient, fake: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.core.config import get_settings

    fake.down = True
    down = await save(client, fx.save_body("fixture-gamma"))
    assert down.status_code == 503 and down.json()["error"]["code"] == "runner_unavailable"
    monkeypatch.setattr(get_settings(), "code_rules_enabled", False)
    off = await save(client, fx.save_body("fixture-gamma"), agent=True)
    assert off.status_code == 409
    assert off.json()["error"]["code"] == "code_rules_disabled"
    assert off.json()["error"]["details"]["setting"] == "CODE_RULES_ENABLED"
    assert count("dw_approvals") == 0 and count("dw_config_versions") == 0


async def test_the_generic_config_store_refuses_and_hides_code_rules(
    client: httpx.AsyncClient, fake: FakeRunner
) -> None:
    refused = await client.post(
        "/api/v1/config-versions",
        json={"kind": "code_rule", "name": "x", "body": {"plugin": "code_rule"}},
    )
    assert refused.status_code == 422
    assert (await save(client, fx.save_body("fixture-gamma"))).status_code == 201
    assert (await client.get("/api/v1/config-versions")).json()["items"] == []
    by_kind = await client.get("/api/v1/config-versions", params={"kind": "code_rule"})
    assert by_kind.json()["items"] == []


async def test_an_agent_whose_hash_was_revoked_after_the_gate_stores_nothing(
    client: httpx.AsyncClient, fake: FakeRunner
) -> None:
    """The body re-reads the state: an agent call that reached it without an approval id (the
    gate saw "approved") while the hash is no longer approved is refused, and nothing is written.
    Driven through the route function itself, as the window between gate and body allows."""
    from src.core.agent_origin import EXECUTORS, Actor
    from src.core.database import async_session_factory
    from src.schemas.code_rules import CodeRuleSaveIn
    from src.services.code_rules.errors import CodeRuleError

    body = fx.save_body("fixture-gamma")
    route = EXECUTORS["src.api.v1.endpoints.code_rules.save_code_rule"].fn
    async with async_session_factory()() as db:
        with pytest.raises(CodeRuleError) as caught:
            await route(
                body=CodeRuleSaveIn.model_validate(body),
                db=db,
                actor=Actor(origin="agent", agent="agent:dataworks-mcp"),
            )
    assert caught.value.code == "code_rule_not_approved"
    assert count("dw_config_versions") == 0 and count("dw_code_rule_approvals") == 0


# --- 018 Review R2 ---------------------------------------------------------------------------------


async def test_a_save_whose_tail_found_no_runner_slot_is_the_runners_fault_not_the_authors(
    client: httpx.AsyncClient, fake: FakeRunner
) -> None:
    """The runner's lane backstop is per REQUEST: a save's last unit can read
    rule_worker_unavailable while its twin ran. That is runner capacity - never
    ``code_rule_nondeterministic`` or ``code_rule_case_failed`` blamed on the author."""
    fake.starve_after = 3  # 2 cases x repeat 2 = 4 units; the 4th finds no slot
    response = await save(client, fx.save_body("fixture-gamma"))
    assert response.status_code == 503, response.text
    error = response.json()["error"]
    assert error["code"] == "runner_unavailable"
    assert "no free slot" in error["details"]["runner"]
    assert error["details"]["next"]["tool"] in await live_tools()
    assert count("dw_config_versions") == 0 and count("dw_code_rule_approvals") == 0
    # The same request with a free runner saves: nothing about the code was wrong.
    fake.starve_after = None
    assert (await save(client, fx.save_body("fixture-gamma"))).status_code == 201


@pytest.mark.parametrize(
    ("code", "limit", "setting"),
    [
        ("limit_above_ceiling", "wall_ms", "CODE_RULE_WALL_MS"),
        ("limit_above_ceiling", "memory_bytes", "CODE_RULE_MEMORY_BYTES"),
        ("limit_below_floor", "prelude_fuel", None),
    ],
)
async def test_a_limit_the_runner_refuses_is_named_not_unreachable(
    client: httpx.AsyncClient, fake: FakeRunner, code: str, limit: str, setting: str | None
) -> None:
    """A setting raised past the runner's RUNNER_MAX_* ceiling: the save says which setting,
    instead of "the runner cannot be reached" (the miStudio 040 D2 analogue)."""
    fake.refuse_run = (422, code, f"limits.{limit} is above this runner's ceiling")
    response = await save(client, fx.save_body("fixture-gamma"), agent=True)
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "runner_limit_refused"
    assert (error["details"]["limit"], error["details"]["setting"]) == (limit, setting)
    assert error["details"]["runner_code"] == code
    assert error["details"]["next"]["tool"] in await live_tools()
    assert count("dw_approvals") == 0 and count("dw_config_versions") == 0


async def test_the_operators_own_save_is_refused_while_code_rules_are_switched_off(
    client: httpx.AsyncClient, fake: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CODE_RULES_ENABLED binds the operator too: the body's own check, not only the agent's
    ``before`` hook (an operator call never passes through the gate's hooks)."""
    from src.core.config import get_settings

    monkeypatch.setattr(get_settings(), "code_rules_enabled", False)
    off = await save(client, fx.save_body("fixture-gamma"))
    assert off.status_code == 409 and off.json()["error"]["code"] == "code_rules_disabled"
    assert fake.requests == [], "nothing reached the runner"
    assert count("dw_config_versions") == 0 and count("dw_code_rule_approvals") == 0


async def test_an_agents_card_says_when_a_hash_was_never_decided(
    client: httpx.AsyncClient, fake: FakeRunner
) -> None:
    """018 Review R2 F5's facts: the card carries the hash's standing NOW and its last decision.
    Since D60 a REVOKED hash never reaches a card (any save of it is refused,
    ``test_revoked_hashes.py``); a hash never decided says so."""
    fresh = await save(client, fx.save_body("fixture-delta"), agent=True)
    assert fresh.status_code == 202, fresh.text
    payload = (await client.get(f"/api/v1/approvals/{fresh.json()['approval_id']}")).json()
    assert payload["payload"]["facts"]["approval_state"] == "unknown"
    assert payload["payload"]["facts"]["last_decision"] is None
