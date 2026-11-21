"""A list result is ONE content block, through the LIVE server (PII run L5 and F-6, 2026-10-20).

The SDK renders a returned list as one block per item, so an empty list reached the client as
``content: []``: it could not be told from a dropped response, and the run's own client crashed on
it. ``server.wrap_list_results`` serialises every list. These tests go through ``build_server``
(the registry production serves) and the real ``DataworksClient`` over an ``httpx`` mock transport,
so removing the wrapping line in ``build_server`` turns them red.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from src.mcp_server.config import MCPSettings
from src.mcp_server.health_gate import HealthGate
from src.mcp_server.server import build_server
from tests.support.mcp_ledger import EXPECTED_CALLS, PRE_READS


@pytest.fixture(autouse=True)
def _open_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    async def unavailable(self: Any, *_: str) -> None:
        return None

    monkeypatch.setattr(HealthGate, "unavailable", unavailable)


def _server(answer: Any) -> tuple[Any, list[httpx.Request]]:
    mcp, client = build_server(MCPSettings(auth_token="x" * 32))
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/v1/sources/meta":
            return httpx.Response(200, json={"limits": {"upload_max_bytes": 10_000_000}})
        return httpx.Response(200, json=answer)

    client._http = httpx.AsyncClient(  # noqa: SLF001 - the transport is the only stand-in
        base_url="http://backend/api/v1",
        transport=httpx.MockTransport(handler),
        headers={"X-Dataworks-Agent": client.identity},
    )
    return mcp, seen


def _blocks(result: Any) -> list[str]:
    return [c.text for c in result.content]


def test_an_empty_list_is_one_block_saying_empty() -> None:
    mcp, seen = _server([])
    result = asyncio.run(mcp.call_tool("dataworks_list_rubrics", {}))
    assert _blocks(result) == ["[]"]
    assert [(r.method, r.url.path) for r in seen] == [("GET", "/api/v1/rubrics")]


def test_a_list_of_two_is_one_block_holding_the_array() -> None:
    mcp, _ = _server([{"id": "rb_1"}, {"id": "rb_2"}])
    result = asyncio.run(mcp.call_tool("dataworks_list_rubrics", {}))
    blocks = _blocks(result)
    assert len(blocks) == 1
    assert json.loads(blocks[0]) == [{"id": "rb_1"}, {"id": "rb_2"}]


def test_a_dict_result_is_unchanged() -> None:
    mcp, _ = _server({"items": [], "total": 0})
    result = asyncio.run(mcp.call_tool("dataworks_list_sources", {}))
    blocks = _blocks(result)
    assert len(blocks) == 1 and json.loads(blocks[0]) == {"items": [], "total": 0}


@pytest.mark.parametrize("tool", sorted(EXPECTED_CALLS))
def test_every_tool_answers_an_empty_list_with_exactly_one_block(tool: str) -> None:
    """Any tool whose route answers ``[]`` gives the client one block, never zero."""
    if tool in PRE_READS:
        pytest.skip("its pre-read answers a dict; the documented call is covered by the others")
    _, _, args, _ = EXPECTED_CALLS[tool]
    mcp, seen = _server([])
    result = asyncio.run(mcp.call_tool(tool, dict(args)))
    assert len(result.content) == 1, (tool, result.content)
    assert len(seen) >= 1
