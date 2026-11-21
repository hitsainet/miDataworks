"""Dry runs (FR-018.27; FTASKS 10.6, 10.10): the caller's texts, nothing stored, rate-limited."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy import text

from src.core.database import Base, get_sync_engine
from src.runner_contract.hashing import code_sha256
from tests.support import code_rule_fixtures as fx
from tests.support.fake_runner import FakeRunner

from .conftest import AGENT, CR, count, live_tools, save

TEST = CR + "/test"
pytestmark = pytest.mark.usefixtures("operator_name")


def _row_counts() -> dict[str, int]:
    with get_sync_engine().connect() as conn:
        return {
            t.name: int(
                conn.execute(text(f"SELECT count(*) FROM {t.name}")).scalar_one()
            )  # noqa: S608
            for t in Base.metadata.sorted_tables
        }


def _inline(behaviour: str, **over: Any) -> dict[str, Any]:
    f = fx.fixture(behaviour)
    body = {"document": f["document"], "cases": f["cases"]}
    body.update(over)
    return body


async def test_a_dry_run_runs_the_cases_and_your_texts_and_stores_nothing(
    client: httpx.AsyncClient, fake: FakeRunner, behaviour: str
) -> None:
    f = fx.fixture(behaviour)
    function = f["document"]["functions"][0]["name"]
    params = f["cases"][0]["params"]
    texts = ["first text", "lone \ud800 surrogate"]
    before = _row_counts()
    import json

    response = await client.post(  # json.dumps escapes the lone surrogate (httpx's JSON would not)
        TEST,
        content=json.dumps(_inline(behaviour, texts=texts, params=params)).encode(),
        headers={**AGENT, "content-type": "application/json"},
    )
    assert response.status_code == 200, response.text  # an agent needs no approval to try
    out = response.json()
    after = _row_counts()
    # The one row is the agent-activity record the middleware writes for EVERY agent request: the
    # route TEMPLATE, never an argument value (FR-018.25 keeps it as it is).
    assert after.pop("dw_agent_requests") == before.pop("dw_agent_requests") + 1
    assert after == before, "a dry run stores nothing, anywhere"
    with get_sync_engine().connect() as conn:
        (logged,) = conn.execute(text("SELECT * FROM dw_agent_requests")).mappings()
    assert logged["route"] == "/api/v1/code-rules/test"
    assert "surrogate" not in str(dict(logged)) and "first text" not in str(dict(logged))
    assert count("dw_approvals") == 0
    assert out["code_sha256"] == code_sha256(f["document"])
    assert [c["id"] for c in out["cases"]] == [c["id"] for c in f["cases"]]
    assert all(c["passed"] for c in out["cases"]) and out["all_cases_passed"] is True
    assert [t["index"] for t in out["texts"]] == [0, 1]
    assert set(out["texts"][0]["results"]) == {function}
    assert out["next"]["tool"] == "dataworks_save_code_rule"
    assert out["next"]["tool"] in await live_tools()
    (run,) = fake.runs()
    from src.services.code_rules import limits

    assert (run["mode"], run["lane"]) == ("dry_run", "interactive")
    assert run["limits"] == limits.in_force()
    assert len(run["calls"]) == len(f["cases"]) + 1
    assert run["calls"][-1] == {
        "function": function,
        "params": params,
        "texts": ["first text", "lone � surrogate"],  # the client's text rule
        "repeat": 1,
    }


async def test_a_dry_run_runs_at_the_limits_in_force_not_the_floor(
    client: httpx.AsyncClient, fake: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.core.config import get_settings
    from src.services.code_rules import limits

    raised = limits.floor()["call_fuel"] * 2
    monkeypatch.setattr(get_settings(), "code_rule_call_fuel", raised)
    assert (await client.post(TEST, json=_inline("fixture-gamma"))).status_code == 200
    (run,) = fake.runs()
    assert run["limits"]["call_fuel"] == raised
    assert run["limits"] == limits.in_force()


async def test_a_stored_version_can_be_tried_by_name(
    client: httpx.AsyncClient, fake: FakeRunner
) -> None:
    body = fx.save_body("fixture-delta")
    assert (await save(client, body)).status_code == 201
    response = await client.post(
        TEST,
        json={"name": body["name"], "version": 1, "texts": ["SO LOUD"], "params": {"min": 2}},
    )
    assert response.status_code == 200, response.text
    assert response.json()["texts"][0]["results"]["loud_words"]["count"] == 2


async def test_diagnostics_only_on_a_dry_run(client: httpx.AsyncClient, fake: FakeRunner) -> None:
    """A raising rule: type, line and a short message in a dry run; the save path keeps the
    reason only even if a runner sent more (M8's API half)."""
    body = fx.save_body("fixture-gamma")
    sha = code_sha256(body["document"])

    def boom(text: str, params: dict[str, Any]) -> int:
        raise ValueError("boom")

    fake.twins[sha] = {"digit_sum": boom}
    dry = await client.post(TEST, json=_inline("fixture-gamma", texts=["x"], params={"sums": [1]}))
    result = dry.json()["texts"][0]["results"]["digit_sum"]
    assert result["reason"] == "rule_error"
    assert result["diagnostic"] == {"exception_type": "ValueError", "line": 3, "message": "boom"}
    fake.diagnostics_everywhere = True
    refused = await save(client, body, agent=True)
    error = refused.json()["error"]
    assert error["code"] == "code_rule_case_failed"
    assert error["details"]["actual"] == {"reason": "rule_error"}, "no diagnostic on a save"


@pytest.mark.parametrize(
    ("over", "status", "code"),
    [
        ({"texts": ["t"] * 21}, 413, "cap_exceeded"),
        ({"params": {"sums": "no"}, "texts": ["1"]}, 422, "code_rule_params_invalid"),
        ({"functions": ["elsewhere"], "texts": ["1"]}, 422, "code_rule_function_missing"),
        ({"name": "gamma-digit-sum"}, 422, "code_rule_invalid"),
        # Nothing to run is a named refusal, not a crash (found by the log guard, 2026-10-24).
        ({"cases": []}, 422, "code_rule_cases_missing"),
    ],
)
async def test_a_dry_run_refusal_is_named(
    client: httpx.AsyncClient, fake: FakeRunner, over: dict[str, Any], status: int, code: str
) -> None:
    response = await client.post(TEST, json=_inline("fixture-gamma", **over))
    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert error["code"] == code
    assert error["details"]["next"]["tool"] in await live_tools()
    assert fake.runs() == []


async def test_switched_off_runner_down_and_rate_limited(
    client: httpx.AsyncClient, fake: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.core.config import get_settings

    monkeypatch.setattr(get_settings(), "code_rule_dry_run_per_minute", 2)
    for _ in range(2):
        assert (await client.post(TEST, json=_inline("fixture-gamma"))).status_code == 200
    third = await client.post(TEST, json=_inline("fixture-gamma"))
    assert third.status_code == 429
    assert third.json()["error"]["details"]["setting"] == "CODE_RULE_DRY_RUN_PER_MINUTE"
    assert len(fake.runs()) == 2, "the refused call never reached the runner"

    monkeypatch.setattr(get_settings(), "code_rule_dry_run_per_minute", 60)
    fake.down = True
    down = await client.post(TEST, json=_inline("fixture-gamma"))
    assert down.status_code == 503 and down.json()["error"]["code"] == "runner_unavailable"

    monkeypatch.setattr(get_settings(), "code_rules_enabled", False)
    off = await client.post(TEST, json=_inline("fixture-gamma"))
    assert off.status_code == 409 and off.json()["error"]["code"] == "code_rules_disabled"


async def test_redis_down_refuses_the_dry_run(
    client: httpx.AsyncClient, fake: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FTID I11: the limiter fails CLOSED."""
    from src.core.config import get_settings

    monkeypatch.setattr(get_settings(), "redis_url", "redis://127.0.0.1:1/0")
    response = await client.post(TEST, json=_inline("fixture-gamma"))
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "rate_limiter_unavailable"
    assert fake.runs() == []


async def test_a_dry_run_says_when_the_runner_had_no_free_slot(
    client: httpx.AsyncClient, fake: FakeRunner
) -> None:
    """018 Review R2: calls past the runner's per-request lane backstop read
    rule_worker_unavailable; the answer says it was the runner's capacity and points at health,
    never "correct the rule"."""
    fake.starve_after = 1
    starved = await client.post(
        TEST, json=_inline("fixture-gamma", texts=["12"], params={"sums": [3]})
    )
    assert starved.status_code == 200, starved.text
    out = starved.json()
    assert out["runner_capacity_exhausted"] is True
    assert out["next"]["tool"] == "dataworks_health"
    assert out["cases"][1]["result"] == {"reason": "rule_worker_unavailable"}
    fake.starve_after = None
    fed = (await client.post(TEST, json=_inline("fixture-gamma"))).json()
    assert fed["runner_capacity_exhausted"] is False
    assert fed["next"]["tool"] == "dataworks_save_code_rule"


async def test_a_limit_past_the_runners_ceiling_is_named_on_a_dry_run(
    client: httpx.AsyncClient, fake: FakeRunner
) -> None:
    fake.refuse_run = (
        422,
        "limit_above_ceiling",
        "limits.call_fuel is above this runner's ceiling",
    )
    response = await client.post(TEST, json=_inline("fixture-gamma"))
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "runner_limit_refused"
    assert error["details"]["setting"] == "CODE_RULE_CALL_FUEL"
