"""MCP gap G-a (2026-11-25): an agent proposes an endpoint-role change, the operator decides it.

FU-7a found no tool that changes an endpoint role's model: the judge's switch took a REST ``PUT``
with the agent header. ``dataworks_propose_endpoint_role`` files that same ``config_write``
approval. Driven through the PRODUCTION registry (``build_server``) over the in-process app, on a
real PostgreSQL: the tool is listed there as a gated write, reads the role once and sends one
``PUT`` with the role's current values for every field it was not given, never sends or answers a
key, and returns the shared approval object with ``next_steps`` naming
``dataworks_get_approval_status``. A change the route would refuse files nothing, because the route
now refuses it before the gate (before 2026-11-25 such a write was filed and then failed at
approval).
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy import func, select

from src.core.database import sync_session_factory
from src.mcp_server.context import GATED_TOOLS, SECRET_PARAMS
from src.models.approval import Approval
from src.models.endpoint_role import EndpointRole
from tests.support.mcp_live import call, production_mcp, refusal_of

TOOL = "dataworks_propose_endpoint_role"
KEY = "sk-operator-judge-key-41c9"
JUDGE = {
    "protocol": "openai_chat",
    "base_url": "http://miserver.test/v1",
    "model_id": "Llama-3.1-8B-Instruct",
    "api_key": KEY,
}


def _approvals() -> int:
    with sync_session_factory()() as db:
        return int(db.execute(select(func.count()).select_from(Approval)).scalar_one())


def _approval(approval_id: str) -> Approval:
    with sync_session_factory()() as db:
        return db.execute(select(Approval).where(Approval.id == approval_id)).scalar_one()


def _ciphertext(role: str) -> bytes | None:
    with sync_session_factory()() as db:
        row = db.get(EndpointRole, role)
        return None if row is None else row.api_key_ciphertext


class Counting:
    """Records every request the tool's client sends, then sends it (only counting is added)."""

    def __init__(self, client: Any) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        for method in ("get", "put", "post", "delete"):
            original = getattr(client, method)

            def make(name: str, fn: Any) -> Any:
                async def recorded(path: str, *args: Any, **kwargs: Any) -> Any:
                    self.calls.append((name.upper(), path, kwargs.get("json_body")))
                    return await fn(path, *args, **kwargs)

                return recorded

            setattr(client, method, make(method, original))


async def _configured_judge(client: httpx.AsyncClient) -> None:
    response = await client.put("/api/v1/endpoint-roles/judge", json=JUDGE)
    assert response.status_code == 200, response.text


def test_the_tool_is_a_gated_write_in_the_live_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    mcp, _ = production_mcp(monkeypatch)
    tool = {t.name: t for t in mcp._tool_manager.list_tools()}[TOOL]  # noqa: SLF001
    assert tool.annotations is not None
    assert (tool.annotations.read_only_hint, tool.annotations.destructive_hint) == (False, False)
    assert GATED_TOOLS[TOOL] == "config_write"
    # No key can be given, so none can be sent, logged or echoed.
    assert not {"api_key", "key", "token"} & set(tool.parameters["properties"])
    assert TOOL not in SECRET_PARAMS
    assert tool.parameters["additionalProperties"] is False


async def test_a_proposal_files_one_config_write_and_changes_nothing_yet(
    client: httpx.AsyncClient, operator_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _configured_judge(client)
    before_key = _ciphertext("judge")
    mcp, tool_client = production_mcp(monkeypatch)
    seen = Counting(tool_client)
    before = _approvals()

    result = await call(mcp, TOOL, {"role": "judge", "model_id": "Qwen2.5-7B-Instruct"})
    answer = result.structured_content
    text = result.content[0].text

    # One read of the role, then one PUT with the whole configuration; nothing else.
    assert [(m, p) for m, p, _ in seen.calls] == [
        ("GET", "/endpoint-roles/judge"),
        ("PUT", "/endpoint-roles/judge"),
    ]
    assert seen.calls[1][2] == {
        "model_id": "Qwen2.5-7B-Instruct",
        "base_url": JUDGE["base_url"],
        "protocol": JUDGE["protocol"],
        "use_mode": "own",
        "inherit_from_judge": False,
    }
    # The shared approval shape, and the next call.
    assert answer["status"] == "pending" and answer["action"] == "config_write"
    approval_id = answer["approval_id"]
    assert answer["approval"]["id"] == approval_id
    assert answer["approval"]["decide_in"] == "midataworks"
    assert answer["approval"]["then"] == "server_runs"
    assert "Qwen2.5-7B-Instruct" in answer["approval"]["summary"]
    assert answer["next_steps"][0] == {
        "tool": "dataworks_get_approval_status",
        "args": {"approval_id": approval_id},
        "why": answer["next_steps"][0]["why"],
    }
    # Exactly one approval filed; its stored request carries no key at all.
    assert _approvals() == before + 1
    row = _approval(approval_id)
    assert row.action == "config_write" and row.status == "pending"
    assert row.payload["body"]["api_key"] is None and row.secret_payload is None
    # Nothing changed until the operator approves; no key, raw or masked, is in the answer.
    role = (await client.get("/api/v1/endpoint-roles/judge")).json()
    assert role["model_id"] == JUDGE["model_id"]
    masked = role["api_key"]
    assert KEY not in text and masked not in text and "api_key" not in text
    assert KEY not in json.dumps(row.payload) and KEY not in row.summary

    # Approved, the model changes and the operator's key is kept byte for byte.
    approved = await client.post(f"/api/v1/approvals/{approval_id}/approve")
    assert approved.status_code == 200 and approved.json()["status"] == "executed", approved.text
    role = (await client.get("/api/v1/endpoint-roles/judge")).json()
    assert role["model_id"] == "Qwen2.5-7B-Instruct"
    assert role["base_url"] == JUDGE["base_url"] and role["protocol"] == JUDGE["protocol"]
    assert _ciphertext("judge") == before_key is not None


REFUSED = [
    ({"role": "judge", "protocol": "openai_scoring"}, "UNKNOWN_PROTOCOL"),
    ({"role": "classifier", "use_mode": "none"}, "USE_MODE_NOT_ALLOWED"),
    ({"role": "judge", "inherit_from_judge": True}, "INHERIT_NOT_ALLOWED"),
    ({"role": "judge", "base_url": "ftp://miserver.test/v1"}, "BAD_URL"),
]


@pytest.mark.parametrize(("args", "code"), REFUSED, ids=[c for _, c in REFUSED])
async def test_an_argument_the_route_refuses_files_nothing(
    client: httpx.AsyncClient,
    operator_name: str,
    monkeypatch: pytest.MonkeyPatch,
    args: dict[str, Any],
    code: str,
) -> None:
    await _configured_judge(client)
    mcp, _ = production_mcp(monkeypatch)
    before = _approvals()
    with pytest.raises(ToolError) as refused:
        await call(mcp, TOOL, args)
    assert refusal_of(refused.value)[:2] == (code, 422)
    assert _approvals() == before, "a write the route refuses was filed for the operator"


async def test_the_route_refuses_before_the_gate_for_any_agent(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    """The refusal lives in REST (C6), so miWorkbench's proxy and a raw REST caller get it too: a
    key without a server, which the operator's approval would have run into KEY_NEEDS_URL."""
    before = _approvals()
    response = await client.put(
        "/api/v1/endpoint-roles/judge",
        json={"api_key": "sk-new"},
        headers={"X-Dataworks-Agent": "agent:mistudio-mcp"},
    )
    assert response.status_code == 422 and response.json()["error"]["code"] == "KEY_NEEDS_URL"
    assert _approvals() == before


async def test_a_call_naming_no_change_is_refused(
    client: httpx.AsyncClient, operator_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, tool_client = production_mcp(monkeypatch)
    seen = Counting(tool_client)
    for args, code in (
        ({"role": "judge"}, "NO_CHANGE_NAMED"),
        (
            {"role": "judge", "price_per_call_usd": 0.0, "clear_price": True},
            "PRICE_AND_CLEAR_PRICE",
        ),
    ):
        with pytest.raises(ToolError) as refused:
            await call(mcp, TOOL, args)
        assert refusal_of(refused.value)[:2] == (code, 422)
    assert seen.calls == [] and _approvals() == 0
