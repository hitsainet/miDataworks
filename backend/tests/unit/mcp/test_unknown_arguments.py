"""Every tool REFUSES an argument name it does not declare (2026-11-11).

The SDK's argument models ignore extra keys: a misplaced or misspelt name was dropped and the call
ran on its defaults. miWorkbench's server changed GPU work that way on 2026-11-10; this server did the
same (``dataworks_list_rubrics`` with ``bogus_name`` sent ``GET /rubrics?page=1&limit=50``).
``server.refuse_unknown_arguments`` closes it for the whole registry in ``build_server``.

Every test goes through ``build_server`` (the registry production serves) with only the network
replaced, so removing the one wiring line there turns them red. The wire test drives the app
``__main__`` builds.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from src.mcp_server.config import MCPSettings
from src.mcp_server.context import SECRET_PARAMS
from src.mcp_server.health_gate import HealthGate
from src.mcp_server.server import build_server
from tests.support.mcp_harness import tool_context
from tests.support.mcp_ledger import EXPECTED_CALLS
from tests.unit.mcp.test_http_transport import _rpc, serving
from tests.unit.mcp.test_http_transport import app as app  # noqa: F401 - the fixture

UNKNOWN = "not_an_argument_of_this_tool"


@pytest.fixture(autouse=True)
def _open_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    async def unavailable(self: Any, *_: str) -> None:
        return None

    monkeypatch.setattr(HealthGate, "unavailable", unavailable)


def _server() -> tuple[Any, list[httpx.Request]]:
    """The production registry; every backend request is recorded and answered ``{}``."""
    mcp, client = build_server(MCPSettings(auth_token="x" * 32))
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    client._http = httpx.AsyncClient(  # noqa: SLF001 - the transport is the only stand-in
        base_url="http://backend/api/v1",
        transport=httpx.MockTransport(handler),
        headers={"X-Dataworks-Agent": client.identity},
    )
    return mcp, seen


def _call(mcp: Any, name: str, arguments: dict[str, Any]) -> Any:
    return asyncio.run(
        mcp._tool_manager.call_tool(name, arguments, tool_context(mcp))
    )  # noqa: SLF001


def _tools(mcp: Any) -> list[Any]:
    return mcp._tool_manager.list_tools()  # noqa: SLF001


def test_every_registered_tool_refuses_an_unknown_name_and_runs_nothing() -> None:
    mcp, seen = _server()
    tools = _tools(mcp)
    assert len(tools) >= 200, "the registry is not the production one"
    ran: list[str] = []
    unnamed: list[str] = []
    for tool in tools:
        try:
            _call(mcp, tool.name, {UNKNOWN: 1})
        except ToolError as exc:
            text = str(exc)
            accepted = sorted(tool.parameters.get("properties", {}))
            if UNKNOWN not in text or any(name not in text for name in accepted):
                unnamed.append(f"{tool.name}: {text[:300]}")
        else:
            ran.append(tool.name)
    assert not ran, f"tools that ACCEPTED an unknown argument name: {ran}"
    assert not unnamed, "refusals that do not name the unknown and accepted names:\n" + "\n".join(
        unnamed
    )
    assert seen == [], f"a refused call reached the backend: {[str(r.url) for r in seen]}"


def test_the_refusal_names_the_unknown_and_the_accepted_names() -> None:
    mcp, seen = _server()
    with pytest.raises(ToolError) as caught:
        _call(mcp, "dataworks_list_rubrics", {"pgae": 2, "behavior_key": "pii"})
    text = str(caught.value)
    assert "unknown argument(s) behavior_key, pgae" in text
    assert "dataworks_list_rubrics accepts behaviour_key, limit, page" in text
    assert seen == []


def test_a_declared_name_still_runs_with_its_value() -> None:
    mcp, seen = _server()
    _call(mcp, "dataworks_list_rubrics", {"page": 3})
    assert [(r.method, r.url.path, dict(r.url.params)) for r in seen] == [
        ("GET", "/api/v1/rubrics", {"page": "3", "limit": "50"})
    ]


def test_the_renamed_probe_list_tool_refuses_an_unknown_name_and_still_runs() -> None:
    """Feature 020 (FR-020.17): the tool renamed at the rebrand's P4 is in the production registry
    under its new name, refuses a name it does not declare, and runs with a declared one."""
    mcp, seen = _server()
    with pytest.raises(ToolError) as caught:
        _call(mcp, "dataworks_list_miserver_probes", {"probe_kind": "humor"})
    text = str(caught.value)
    assert "unknown argument(s) probe_kind" in text
    assert "dataworks_list_miserver_probes accepts limit, page" in text
    assert seen == []
    _call(mcp, "dataworks_list_miserver_probes", {"limit": 5})
    assert [(r.method, r.url.path, dict(r.url.params)) for r in seen] == [
        ("GET", "/api/v1/labeling/probes", {"page": "1", "limit": "5"})
    ]


def test_every_documented_call_is_accepted_by_the_production_argument_models() -> None:
    """The ledger's arguments pass the strict models: no documented call carries a stray name
    (the harness behind ``test_reachability`` registers modules directly, without this check)."""
    mcp, _ = _server()
    registered = {t.name: t for t in _tools(mcp)}
    refused: list[str] = []
    for tool, (_, _, kwargs, _) in sorted(EXPECTED_CALLS.items()):
        meta = registered[tool].fn_metadata
        try:
            meta.arg_model.model_validate(meta.pre_parse_json(kwargs))
        except ValueError as exc:
            refused.append(f"{tool}: {str(exc)[:300]}")
    assert not refused, "documented calls the production models refuse:\n" + "\n".join(refused)


@pytest.mark.parametrize("tool", sorted(SECRET_PARAMS))
def test_a_refusal_never_echoes_a_secret_value(tool: str) -> None:
    mcp, seen = _server()
    # Short on purpose: pydantic cuts a long input's repr in the middle, which would hide a
    # long secret from this check while still printing most of it.
    secret = "s3cr9Zq"
    arguments = dict.fromkeys(SECRET_PARAMS[tool], secret) | {UNKNOWN: secret}
    with pytest.raises(ToolError) as caught:
        _call(mcp, tool, arguments)
    assert UNKNOWN in str(caught.value)
    assert secret not in str(caught.value)
    assert "input_value" not in str(caught.value)
    assert seen == []


def test_an_invalid_secret_value_is_not_echoed_either() -> None:
    """The same models validate every value: before 2026-11-11 a token of the wrong type came back
    in the error text (pydantic's ``input_value``)."""
    mcp, seen = _server()
    secret = "s3cr9Zq"
    with pytest.raises(ToolError) as caught:
        _call(mcp, "dataworks_set_hf_token", {"token": [secret]})
    assert "valid string" in str(caught.value)
    assert secret not in str(caught.value)
    assert seen == []


def test_the_published_schema_says_no_other_names() -> None:
    mcp, _ = _server()
    loose = [
        t.name
        for t in asyncio.run(mcp.list_tools())
        if t.input_schema.get("additionalProperties") is not False
    ]
    assert not loose, f"input schemas that allow other names: {loose}"


async def test_over_the_wire_an_unknown_name_is_an_error_result(
    app, monkeypatch
) -> None:  # noqa: F811
    from src.mcp_server.client import DataworksClient

    calls: list[tuple[str, str]] = []

    async def record(self: DataworksClient, method: str, path: str, **_kw: Any) -> Any:
        calls.append((method, path))
        return []

    monkeypatch.setattr(DataworksClient, "request", record)
    async with serving(app) as client:
        response = await _rpc(
            client,
            "tools/call",
            {"name": "dataworks_list_jobs", "arguments": {"stat": "running"}},
        )
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["isError"] is True, json.dumps(result)[:500]
    assert "unknown argument(s) stat" in result["content"][0]["text"]
    assert calls == []
