"""The approval list: revoke and approve again are the operator's alone (FR-018.29, FR-018.30;
FTASKS 10.2, 10.7). State is read FRESH: a revocation between two reads is seen by the second."""

from __future__ import annotations

import httpx
import pytest

from src.runner_contract.hashing import code_sha256
from tests.support import code_rule_fixtures as fx
from tests.support.fake_runner import FakeRunner

from .conftest import AGENT, CR, count, live_tools, save

pytestmark = pytest.mark.usefixtures("operator_name")


async def _saved(client: httpx.AsyncClient, behaviour: str = "fixture-gamma") -> str:
    body = fx.save_body(behaviour)
    assert (await save(client, body)).status_code == 201
    return code_sha256(body["document"])


async def test_revoke_then_approve_again_with_history(
    client: httpx.AsyncClient, fake: FakeRunner
) -> None:
    sha = await _saved(client)
    revoked = await client.post(f"{CR}/approvals/{sha}/revoke", json={"reason": "too broad"})
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["state"] == "revoked" and revoked.json()["changed"] is True
    listed = (await client.get(CR)).json()["items"][0]
    assert listed["approval_state"] == "revoked"
    # D60: an agent's save of the revoked hash is refused outright (nothing queued).
    again = await save(client, fx.save_body("fixture-gamma"), agent=True)
    assert again.status_code == 409 and again.json()["error"]["code"] == "code_rule_revoked"
    history = (await client.get(f"{CR}/approvals", params={"code_sha256": sha})).json()
    assert history["state"] == "revoked"
    assert history["pending_approvals"] == []
    assert [h["action"] for h in history["history"]] == ["revoke", "approve"]
    assert history["history"][0]["reason"] == "too broad"
    assert history["history"][0]["changed_by"] == "Test Operator"
    restored = await client.post(f"{CR}/approvals/{sha}/approve", json={"reason": "narrowed"})
    assert restored.json()["state"] == "approved"
    assert count("dw_code_rule_approvals", "code_sha256 = :s", s=sha) == 3


async def test_revoking_what_is_not_approved_and_an_unknown_hash_are_named(
    client: httpx.AsyncClient, fake: FakeRunner
) -> None:
    sha = await _saved(client)
    await client.post(f"{CR}/approvals/{sha}/revoke", json={"reason": "r"})
    twice = await client.post(f"{CR}/approvals/{sha}/revoke", json={"reason": "r"})
    assert twice.status_code == 409 and twice.json()["error"]["code"] == "code_rule_not_approved"
    assert twice.json()["error"]["details"]["next"]["tool"] in await live_tools()
    unknown = await client.post(f"{CR}/approvals/{'a' * 64}/approve", json={"reason": "r"})
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "code_rule_not_found"
    no_reason = await client.post(f"{CR}/approvals/{sha}/approve", json={"reason": ""})
    assert no_reason.status_code == 422


@pytest.mark.parametrize("action", ["revoke", "approve"])
async def test_an_agent_is_refused_and_writes_nothing(
    client: httpx.AsyncClient, fake: FakeRunner, action: str
) -> None:
    sha = await _saved(client)
    before = count("dw_code_rule_approvals")
    refused = await client.post(
        f"{CR}/approvals/{sha}/{action}", json={"reason": "agent tries"}, headers=AGENT
    )
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "agent_forbidden"
    assert count("dw_code_rule_approvals") == before
    assert count("dw_approvals") == 0, "an agent's revoke is refused, not queued"


async def test_no_live_tool_reaches_the_revoke_or_approve_routes() -> None:
    """FR-018.30: derived from the LIVE registry and the ledger every tool's call is pinned by."""
    from tests.support.mcp_ledger import EXPECTED_CALLS, full, template_matches

    live = await live_tools()
    for tool, (_method, path, _args, _payload) in EXPECTED_CALLS.items():
        if tool not in live:
            continue
        for template in (
            "/api/v1/code-rules/approvals/{sha}/revoke",
            "/api/v1/code-rules/approvals/{sha}/approve",
        ):
            assert not template_matches(template, full(path)), (tool, path)


async def test_the_state_is_read_fresh_every_time(
    client: httpx.AsyncClient, fake: FakeRunner
) -> None:
    """M15: a revocation written between two reads is seen by the second (no process cache)."""
    from src.core.database import async_session_factory
    from src.services.code_rules import approvals

    sha = await _saved(client)
    async with async_session_factory()() as db:
        assert await approvals.state(db, sha) == "approved"
        await client.post(f"{CR}/approvals/{sha}/revoke", json={"reason": "between reads"})
        assert await approvals.state(db, sha) == "revoked"


async def test_approving_again_an_approved_hash_changes_nothing(
    client: httpx.AsyncClient, fake: FakeRunner
) -> None:
    """018 Review R2: approve-again is a no-op on an approved hash (no duplicate row, changed
    false), so the history stays a list of real decisions."""
    sha = await _saved(client)
    before = count("dw_code_rule_approvals", "code_sha256 = :s", s=sha)
    again = await client.post(f"{CR}/approvals/{sha}/approve", json={"reason": "again"})
    assert again.status_code == 200, again.text
    assert (again.json()["state"], again.json()["changed"]) == ("approved", False)
    assert count("dw_code_rule_approvals", "code_sha256 = :s", s=sha) == before == 1
