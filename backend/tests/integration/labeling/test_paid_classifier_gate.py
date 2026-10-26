"""Paid classifiers always wait for the operator (017 FTASKS 9.3 - 9.7; FR-017.39, FR-017.40,
FR-017.64; FTID §7.7; TD-017.43, TD-017.44).

TypeSafe's fake behind the real routes and engine: a classifier role on a hosted protocol with a
price per call, under ``CLASSIFIER_SPEND_CEILING_USD``. Controls: M19 (the paid rule removed from
``action_for``), the refusals (unpriced, ceiling unset or passed, over the cap), the price write's
PIN and approval, and the question each fixture behaviour sends.
"""

from __future__ import annotations

import dataclasses
import json
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from src.core.config import get_settings
from src.core.database import sync_session_factory
from src.models.approval import Approval
from src.models.endpoint_role import EndpointRole
from src.models.label_run import LabelRun
from tests.integration.labeling.helpers import jev_template_id, texts
from tests.support.behaviour_fixtures import FIXTURES
from tests.support.labeling_fixtures import Labeling, set_role
from tests.support.second_opinion import AGENT, Paid, body, paid, template_id, version

__all__ = ["paid"]

ORIGIN_TEXT = "https://api.typesafe.ai:443"
SENTINEL = "sentinel-row-text-7c1e"


def runs() -> int:
    with sync_session_factory()() as db:
        return int(db.execute(select(func.count()).select_from(LabelRun)).scalar_one())


def approval(approval_id: str) -> Approval:
    with sync_session_factory()() as db:
        row = db.get(Approval, approval_id)
        assert row is not None
        db.expunge(row)
        return row


async def test_an_agent_ten_row_run_on_a_paid_classifier_waits(
    client: httpx.AsyncClient, paid: Paid
) -> None:
    rows = [f"{SENTINEL} {t}" for t in texts(10)]
    v = version(paid.lab, rows)
    t = await template_id(client)
    response = await client.post("/api/v1/label-runs", json=body(v, t), headers=AGENT)
    assert response.status_code == 202, response.text
    pending = response.json()
    assert pending["action"] == "paid_classifier_run" and pending["status"] == "pending"
    assert runs() == 0 and paid.scoring() == [], "nothing runs and nothing is sent before"
    stored = approval(pending["approval_id"])
    # 9.7: the card names the rows, the cap, the cost and where the text goes
    assert stored.payload["paid"] == {
        "rows": 10,
        "cap_rows": 2500,
        "price_per_call_usd": 0.0008,
        "questions": 1,
        "estimated_usd": pytest.approx(0.008),
        "spent_usd": 0.0,
        "ceiling_usd": 25.0,
        "endpoint": ORIGIN_TEXT,
        "text_leaves": f"The selected rows' text is sent to {ORIGIN_TEXT}.",
    }
    assert "10 rows" in stored.summary and ORIGIN_TEXT in stored.summary
    assert SENTINEL not in stored.summary and SENTINEL not in json.dumps(stored.payload)
    # the operator approves: the stored request runs once, recording the approved cost basis
    approved = await client.post(f"/api/v1/approvals/{pending['approval_id']}/approve")
    assert approved.status_code == 200, approved.text
    with sync_session_factory()() as db:
        (run,) = db.execute(select(LabelRun)).scalars()
        assert run.approval_id == pending["approval_id"]
        assert run.cost_basis == {
            "paid": True,
            "protocol": "typesafe_systemone",
            "price_per_call_usd": 0.0008,
            "questions": 1,
            "rows_planned": 10,
            "estimated_usd": pytest.approx(0.008),
        }
        run_id = run.id
    assert paid.lab.run_until_done(run_id).state == "completed"
    assert len(paid.scoring()) == 10


async def test_the_same_run_on_a_local_classifier_starts_and_an_operator_is_never_gated(
    client: httpx.AsyncClient, paid: Paid
) -> None:
    v = version(paid.lab, texts(10))
    operator = await client.post("/api/v1/label-runs", json=body(v, await template_id(client)))
    assert operator.status_code == 201, operator.text  # the operator's start: no approval
    assert operator.json()["cost_basis"]["paid"] is True
    set_role("classifier")  # the fake miLLM, openai_scoring, no price: not paid
    local = await client.post(
        "/api/v1/label-runs", json=body(v, await jev_template_id(client)), headers=AGENT
    )
    assert local.status_code == 201, local.text
    assert local.json()["cost_basis"]["paid"] is False


async def test_the_plan_states_the_cost(client: httpx.AsyncClient, paid: Paid) -> None:
    v = version(paid.lab, texts(12))
    request = json.dumps(body(v, await template_id(client)))
    plan = (await client.get("/api/v1/label-runs/plan", params={"request": request})).json()
    cost = plan["cost"]
    assert cost["paid"] is True and cost["rows_planned"] == 12
    assert cost["estimated_usd"] == pytest.approx(12 * 0.0008)
    assert (cost["cap_rows"], cost["ceiling_usd"], cost["spent_usd"]) == (2500, 25.0, 0.0)
    assert cost["remaining_usd"] == 25.0


@pytest.mark.parametrize(
    ("change", "code", "setting"),
    [
        ("cap", "classifier_cap_exceeded", "CLASSIFIER_RUN_CAP_ROWS"),
        ("unpriced", "classifier_unpriced", None),
        ("no_ceiling", "classifier_spend_ceiling", "CLASSIFIER_SPEND_CEILING_USD"),
        ("past_ceiling", "classifier_spend_ceiling", "CLASSIFIER_SPEND_CEILING_USD"),
    ],
)
async def test_a_paid_run_that_may_not_start_is_refused_for_anyone(
    client: httpx.AsyncClient,
    paid: Paid,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
    code: str,
    setting: str | None,
) -> None:
    settings = get_settings()
    if change == "cap":
        monkeypatch.setattr(settings, "classifier_run_cap_rows", 9)
    elif change == "unpriced":
        paid.priced(None)
    elif change == "no_ceiling":
        monkeypatch.setattr(settings, "classifier_spend_ceiling_usd", None)
    else:
        monkeypatch.setattr(settings, "classifier_spend_ceiling_usd", 0.005)
    v = version(paid.lab, texts(10))
    t = await template_id(client)
    for headers in ({}, AGENT):  # refused, never queued for the operator
        response = await client.post("/api/v1/label-runs", json=body(v, t), headers=headers)
        assert response.status_code == 422, response.text
        error = response.json()["error"]
        assert error["code"] == code, error
        if setting is not None:
            assert error["details"]["setting"] == setting
        assert error["details"]["next"]["tool"]
    with sync_session_factory()() as db:
        assert db.execute(select(func.count()).select_from(Approval)).scalar_one() == 0
    assert runs() == 0 and paid.scoring() == []


async def test_spent_counts_what_paid_runs_scored(
    client: httpx.AsyncClient, paid: Paid, monkeypatch: pytest.MonkeyPatch
) -> None:
    t = await template_id(client)
    first = await client.post("/api/v1/label-runs", json=body(version(paid.lab, texts(10)), t))
    assert paid.lab.run_until_done(first.json()["id"]).state == "completed"
    # 10 rows x 1 question x $0.0008 spent; a ceiling of $0.012 leaves room for 5 more rows
    monkeypatch.setattr(get_settings(), "classifier_spend_ceiling_usd", 0.012)
    v2 = version(paid.lab, texts(6, prefix="more"))
    request = json.dumps(body(v2, t))
    refused = await client.get("/api/v1/label-runs/plan", params={"request": request})
    assert refused.json()["error"]["details"]["spent"] == pytest.approx(0.008)
    v3 = version(paid.lab, texts(5, prefix="few"))
    ok = await client.get("/api/v1/label-runs/plan", params={"request": json.dumps(body(v3, t))})
    assert ok.json()["cost"]["spent_usd"] == pytest.approx(0.008)


async def test_an_agent_may_not_set_the_spend_ceiling(
    client: httpx.AsyncClient, paid: Paid
) -> None:
    response = await client.put(
        "/api/v1/settings/classifier_spend_ceiling_usd", json={"value": "100"}, headers=AGENT
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "AGENT_FORBIDDEN"


async def test_the_price_is_a_pin_gated_settings_write_and_an_agent_write_waits(
    client: httpx.AsyncClient, paid: Paid
) -> None:
    current = (await client.get("/api/v1/endpoint-roles/classifier")).json()
    assert current["price_per_call_usd"] == pytest.approx(0.0008) and current["paid"] is True
    write = {
        "protocol": "typesafe_systemone",
        "base_url": current["base_url"],
        "model_id": None,
        "price_per_call_usd": "0.0012",
    }
    # operator, no PIN set: the write lands; a null price keeps it; clear_price removes it
    stored = await client.put("/api/v1/endpoint-roles/classifier", json=write)
    assert stored.status_code == 200 and stored.json()["price_per_call_usd"] == 0.0012
    kept = await client.put(
        "/api/v1/endpoint-roles/classifier", json={**write, "price_per_call_usd": None}
    )
    assert kept.json()["price_per_call_usd"] == 0.0012
    # a move to another server clears a kept price (it was the old server's)
    moved = await client.put(
        "/api/v1/endpoint-roles/classifier",
        json={**write, "price_per_call_usd": None, "base_url": "https://other.example/v1"},
    )
    assert moved.json()["price_per_call_usd"] is None and moved.json()["paid"] is True
    # an agent's price write waits for the operator (config_write), writing nothing
    agent = await client.put(
        "/api/v1/endpoint-roles/classifier",
        json={**write, "price_per_call_usd": "0"},
        headers=AGENT,
    )
    assert agent.status_code == 202 and agent.json()["action"] == "config_write"
    with sync_session_factory()() as db:
        row = db.get(EndpointRole, "classifier")
        assert row is not None and row.price_per_call_usd is None
    # with a PIN set, the operator's write needs the session
    assert (await client.put("/api/v1/settings/pin", json={"pin": "4711"})).status_code == 200
    refused = await client.put("/api/v1/endpoint-roles/classifier", json=write)
    assert refused.status_code in (401, 403) and refused.json()["error"]["code"] == "PIN_REQUIRED"
    with sync_session_factory()() as db:
        row = db.get(EndpointRole, "classifier")
        assert row is not None and row.price_per_call_usd is None


async def test_a_zero_price_makes_the_hosted_role_unpaid(
    client: httpx.AsyncClient, paid: Paid
) -> None:
    paid.priced("0")
    v = version(paid.lab, texts(10))
    response = await client.post(
        "/api/v1/label-runs", json=body(v, await template_id(client)), headers=AGENT
    )
    assert response.status_code == 201, response.text
    assert response.json()["cost_basis"]["paid"] is False
    with sync_session_factory()() as db:
        assert db.get(EndpointRole, "classifier").price_per_call_usd == Decimal("0")  # type: ignore[union-attr]


@pytest.mark.parametrize("key", ["fixture-alpha", "fixture-beta"])
async def test_each_fixture_behaviour_sends_its_own_question(
    client: httpx.AsyncClient, paid: Paid, key: str
) -> None:
    """FR-017.39: the wording is behaviour data; one run per question, grouped by a hand-off,
    each comparing its own target column."""
    doc: dict[str, Any] = json.loads((FIXTURES / key / "second_opinion.json").read_text())
    columns = list(doc["questions"])
    other = "fixture-beta" if key == "fixture-alpha" else "fixture-alpha"
    foreign = json.loads((FIXTURES / other / "second_opinion.json").read_text())["questions"]
    n = 4
    v = version(
        paid.lab, texts(n, prefix=key), **{c: [i % 2 == 0 for i in range(n)] for c in columns}
    )
    t = await template_id(client)
    for column in columns:
        response = await client.post(
            "/api/v1/label-runs",
            json=body(
                v,
                t,
                question=doc["questions"][column],
                target_column=column,
                handoff_id=f"ho-{key}",
                behaviour_key=key,
            ),
        )
        assert response.status_code == 201, response.text
        run = response.json()
        assert (run["target_column"], run["handoff_id"]) == (column, f"ho-{key}")
        before = len(paid.scoring())
        assert paid.lab.run_until_done(run["id"]).state == "completed"
        sent = paid.scoring()[before:]
        assert len(sent) == n
        for request in sent:
            (question,) = request.body["questions"].values()
            assert question["instructions"] == doc["questions"][column]
            assert all(q not in json.dumps(request.body) for q in foreign.values())


async def test_a_target_column_is_a_column_and_never_the_deployed_truth(
    client: httpx.AsyncClient, paid: Paid
) -> None:
    v = version(paid.lab, texts(4), deployed_truth=[True, False, True, False])
    t = await template_id(client)
    for column, fragment in (("deployed_truth", "veto"), ("absent_column", "no column")):
        response = await client.post("/api/v1/label-runs", json=body(v, t, target_column=column))
        assert response.status_code == 422, response.text
        error = response.json()["error"]
        assert error["code"] == "target_column_invalid" and fragment in error["message"]


async def test_the_local_role_keeps_working_without_a_price(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    """Nothing changes for an unpaid endpoint: no price, no ceiling, no approval for 10 rows."""
    set_role("classifier")
    v = version(labeling, texts(10))
    response = await client.post(
        "/api/v1/label-runs", json=body(v, await jev_template_id(client)), headers=AGENT
    )
    assert response.status_code == 201, response.text


async def test_an_approval_given_before_the_endpoint_became_paid_does_not_run_it(
    client: httpx.AsyncClient, paid: Paid, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An agent's start approved as ``agent_label_rows`` (unpaid, over the window) is refused
    ``PLAN_CHANGED`` when the role is paid by the time the operator approves: a paid run needs
    a ``paid_classifier_run`` approval naming its cost."""
    monkeypatch.setattr(get_settings(), "agent_label_row_threshold", 5)
    paid.priced("0")
    v = version(paid.lab, texts(10))
    t = await template_id(client)
    pending = await client.post("/api/v1/label-runs", json=body(v, t), headers=AGENT)
    assert pending.status_code == 202 and pending.json()["action"] == "agent_label_rows"
    paid.priced("0.0008")
    approved = await client.post(f"/api/v1/approvals/{pending.json()['approval_id']}/approve")
    assert runs() == 0 and paid.scoring() == []
    stored = approval(pending.json()["approval_id"])
    assert stored.status == "failed", (stored.status, approved.text)
    assert (stored.error or {}).get("code") == "PLAN_CHANGED", stored.error
    assert "became paid" in str((stored.error or {}).get("message"))


async def test_an_approved_paid_start_whose_price_rose_does_not_run(
    client: httpx.AsyncClient, paid: Paid
) -> None:
    """Review R4: the operator approved 10 rows at $0.0008 a call; the price rose to $0.5 before
    the approval. The approved terms bind the run: ``PLAN_CHANGED``, nothing sent."""
    v = version(paid.lab, texts(10))
    t = await template_id(client)
    pending = await client.post("/api/v1/label-runs", json=body(v, t), headers=AGENT)
    assert pending.status_code == 202 and pending.json()["action"] == "paid_classifier_run"
    paid.priced("0.5")
    await client.post(f"/api/v1/approvals/{pending.json()['approval_id']}/approve")
    stored = approval(pending.json()["approval_id"])
    assert stored.status == "failed", stored.status
    assert (stored.error or {}).get("code") == "PLAN_CHANGED", stored.error
    assert runs() == 0 and paid.scoring() == []


async def test_an_approved_paid_start_at_the_same_or_a_lower_price_runs(
    client: httpx.AsyncClient, paid: Paid
) -> None:
    v = version(paid.lab, texts(10))
    t = await template_id(client)
    pending = await client.post("/api/v1/label-runs", json=body(v, t), headers=AGENT)
    paid.priced("0.0004")
    await client.post(f"/api/v1/approvals/{pending.json()['approval_id']}/approve")
    assert approval(pending.json()["approval_id"]).status == "executed"
    assert runs() == 1


async def test_a_queued_paid_run_reserves_its_estimate_under_the_ceiling(
    client: httpx.AsyncClient, paid: Paid, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review R4: two runs that each fit under the ceiling must not both start. The first is
    queued (nothing scored yet); its planned $0.008 counts, so a second $0.008 plan under a
    $0.012 ceiling is refused, and fits again once the first has finished within its estimate."""
    monkeypatch.setattr(get_settings(), "classifier_spend_ceiling_usd", 0.012)
    t = await template_id(client)
    first = await client.post("/api/v1/label-runs", json=body(version(paid.lab, texts(10)), t))
    assert first.status_code == 201, first.text
    second = body(version(paid.lab, texts(10, prefix="second")), t)
    refused = await client.post("/api/v1/label-runs", json=second)
    assert refused.status_code == 422, refused.text
    error = refused.json()["error"]
    assert error["code"] == "classifier_spend_ceiling"
    assert error["details"]["spent"] == pytest.approx(0.008)
    assert paid.lab.run_until_done(first.json()["id"]).state == "completed"
    small = body(version(paid.lab, texts(5, prefix="small")), t)
    assert (await client.post("/api/v1/label-runs", json=small)).status_code == 201


async def test_a_paid_start_the_gate_read_as_unpaid_is_still_refused(
    client: httpx.AsyncClient, paid: Paid, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review R4 (R3 survivor 9.6-route-guard): the gate's predicate and the route plan the same
    start twice; a price written between the two reads makes the predicate see an unpaid plan.
    The route's own guard refuses the agent's un-approved paid start (``APPROVAL_REQUIRED``):
    nothing runs, nothing is sent."""
    from src.services import label_run_service

    real = label_run_service.plan
    calls: list[str] = []

    async def stale_first_read(db: Any, req: Any, origin: str) -> Any:
        result = await real(db, req, origin)
        calls.append(origin)
        if len(calls) == 1:  # the predicate's read: the role was unpaid a moment ago
            unpaid = {**(result.plan.cost or {}), "paid": False}
            plan = result.plan.model_copy(update={"cost": unpaid, "approval_needed": False})
            return dataclasses.replace(result, plan=plan)
        return result

    monkeypatch.setattr(label_run_service, "plan", stale_first_read)
    v = version(paid.lab, texts(10))
    response = await client.post(
        "/api/v1/label-runs", json=body(v, await template_id(client)), headers=AGENT
    )
    assert len(calls) == 2, calls
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "APPROVAL_REQUIRED"
    assert runs() == 0 and paid.scoring() == []


async def test_a_judge_on_the_classifiers_server_pays_the_classifiers_price(
    paid: Paid,
) -> None:
    """Review R4 (survivor B-inherited-price): a judge that uses the classifier's endpoint
    (``inherited_from = classifier``) is priced by the classifier's row, so a judge run there is
    paid; the judge's own (unpriced) row is not the server's price."""
    from src.core.database import async_session_factory
    from src.services.endpoint_resolver import ResolvedRoleEndpoint
    from src.services.label_run_service import plan_cost

    judge = ResolvedRoleEndpoint(
        "judge", "openai_chat", "https://api.typesafe.ai/v1", "m", None, "classifier"
    )
    async with async_session_factory()() as db:
        facts = await plan_cost(db, judge, 10)
    assert facts["paid"] is True and facts["price_per_call_usd"] == pytest.approx(0.0008)
    assert facts["estimated_usd"] == pytest.approx(0.008)
