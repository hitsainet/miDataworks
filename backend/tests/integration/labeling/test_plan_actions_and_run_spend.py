"""What a label-run plan says a start would file, and what a run spent (FU-7a MCP gaps (g), (h),
2026-11-27).

(g)/(ad) Live: ``dataworks_plan_label_run`` answered ``approval_needed: true`` and nothing about WHICH
approval; the runbook expected ``paid_classifier_run`` plus ``agent_label_rows`` and the start filed
only the first. ``approval_needed`` was the P-07 window alone, so a paid agent run under the window
read ``false`` while its start waited for the operator (gap (ad) again). The plan now names
``approval_actions``, derived from the start gate's own predicate; ``approval_needed`` says whether
the start waits (the gate reads it), and ``window_approval_needed`` keeps the P-07 window alone.
Every case below starts the same request through MCP and compares the approvals actually filed
with the plan's list.

(h) Live: ``dataworks_get_label_run`` gave only ``cost_basis.estimated_usd``; what the run spent was
read as the spend ledger's move. The run now carries ``spent_usd``, the ledger's own per-run figure
(rows it scored, less the rows it reused, x questions x price), and the ledger is their sum.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy import select

from src.core.config import get_settings
from src.core.database import sync_session_factory
from src.models.approval import Approval
from tests.integration.labeling.helpers import jev_template_id, texts
from tests.support.labeling_fixtures import set_role
from tests.support.mcp_live import call, live_mcp
from tests.support.second_opinion import Paid, body, paid, template_id, version

__all__ = ["paid"]

PRICE = 0.0008


def filed() -> list[str]:
    with sync_session_factory()() as db:
        return [a.action for a in db.execute(select(Approval)).scalars()]


async def _plan_then_start(start: dict[str, Any]) -> tuple[dict[str, Any], Any]:
    mcp, mcp_client = live_mcp()
    try:
        plan = await call(mcp, "dataworks_plan_label_run", start)
        started = await call(mcp, "dataworks_start_label_run", start)
    finally:
        await mcp_client.close()
    return plan, started


@pytest.mark.parametrize(
    ("endpoint", "threshold", "window_needed", "actions"),
    [
        # FU-7a 1.2.3: paid AND over the window: ONE approval, the paid one, covers both
        ("paid", 5, True, ["paid_classifier_run"]),
        # the latent case: paid under the window read approval_needed false, yet it waits
        ("paid", 5000, False, ["paid_classifier_run"]),
        ("local", 5, True, ["agent_label_rows"]),
        ("local", 5000, False, []),
    ],
)
async def test_the_plan_names_exactly_the_approvals_a_start_files(
    client: httpx.AsyncClient,
    paid: Paid,
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
    threshold: int,
    window_needed: bool,
    actions: list[str],
) -> None:
    monkeypatch.setattr(get_settings(), "agent_label_row_threshold", threshold)
    v = version(paid.lab, texts(10))
    if endpoint == "paid":
        template = await template_id(client)
    else:
        set_role("classifier")  # the fake miServer, openai_scoring, no price: not paid
        template = await jev_template_id(client)
    plan, started = await _plan_then_start(body(v, template))
    # (ad): approval_needed says whether the start waits; the P-07 window alone has its own field
    assert plan["window_approval_needed"] is window_needed
    assert plan["approval_needed"] is bool(actions)
    assert plan["approval_actions"] == actions, plan
    assert filed() == actions, "the start filed what the plan named, no more and no fewer"
    if actions:
        assert started["status"] == "pending" and started["action"] == actions[0]
    else:
        assert started["state"] in ("queued", "running", "completed"), started


async def test_an_operators_plan_names_no_approval_even_when_paid(
    client: httpx.AsyncClient, paid: Paid, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "agent_label_row_threshold", 5)
    import json

    start = body(version(paid.lab, texts(10)), await template_id(client))
    plan = (
        await client.get("/api/v1/label-runs/plan", params={"request": json.dumps(start)})
    ).json()
    assert plan["cost"]["paid"] is True and plan["approval_actions"] == []
    assert plan["approval_needed"] is False and plan["window_approval_needed"] is False
    started = await client.post("/api/v1/label-runs", json=start)
    assert started.status_code == 201, started.text
    assert filed() == []


async def test_a_run_reads_what_it_spent_and_the_ledger_is_their_sum(
    client: httpx.AsyncClient, paid: Paid
) -> None:
    from src.services import label_run_service

    # TypeSafe: 10 rows scored at $0.0008
    t = await template_id(client)
    first = await client.post("/api/v1/label-runs", json=body(version(paid.lab, texts(10)), t))
    assert first.status_code == 201, first.text
    assert paid.lab.run_until_done(first.json()["id"]).state == "completed"
    # a PRICED local classifier (it reports its revision, so labels are reused): 4 rows, then the
    # whole 12-row version, which reuses those 4 and scores 8 at $0.0005
    set_role("classifier", price="0.0005")
    jev = await jev_template_id(client)
    v2 = version(paid.lab, texts(12, prefix="loc"))
    part = await client.post("/api/v1/label-runs", json=body(v2, jev, row_filter={"max_rows": 4}))
    assert part.status_code == 201, part.text
    assert paid.lab.run_until_done(part.json()["id"]).state == "completed"
    whole = await client.post("/api/v1/label-runs", json=body(v2, jev))
    assert whole.status_code == 201, whole.text
    assert paid.lab.run_until_done(whole.json()["id"]).state == "completed"

    mcp, mcp_client = live_mcp()
    try:
        reads = [
            await call(mcp, "dataworks_get_label_run", {"run_id": r.json()["id"]})
            for r in (first, part, whole)
        ]
    finally:
        await mcp_client.close()
    one, two, three = reads
    assert one["spent_usd"] == pytest.approx(10 * PRICE)
    assert two["spent_usd"] == pytest.approx(4 * 0.0005)
    assert (three["rows_done"], three["rows_reused"]) == (12, 4), three
    assert three["spent_usd"] == pytest.approx(8 * 0.0005), "reused rows cost nothing"
    with sync_session_factory()() as db:
        ledger = label_run_service.spent_usd_sync(db)
    assert ledger == pytest.approx(sum(r["spent_usd"] for r in reads))

    # a run on an unpriced local classifier spent nothing, and says so
    set_role("classifier")
    local = await client.post(
        "/api/v1/label-runs", json=body(version(paid.lab, texts(4, prefix="free")), jev)
    )
    assert local.status_code == 201, local.text
    assert paid.lab.run_until_done(local.json()["id"]).state == "completed"
    read = (await client.get(f"/api/v1/label-runs/{local.json()['id']}")).json()
    assert read["cost_basis"]["paid"] is False and read["spent_usd"] == 0.0
