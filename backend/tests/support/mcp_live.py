"""The MCP tools over the REAL backend, in process (017: "read back through MCP").

Every category module is registered against a real ``DataworksClient`` whose HTTP client is swapped
for one on ``httpx.ASGITransport`` over the FastAPI app: a tool's call goes through the client's
own request code, the agent header, the route, the service and the database, and the answer comes
back through the tool. Only the network is replaced (the swap ``tests/unit/mcp/test_client.py``
makes). Requests carry ``agent:dataworks-mcp``: gated actions wait for approval as they would.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer

from src.mcp_server.client import AGENT_HEADER, DataworksClient
from src.mcp_server.config import MCPSettings
from src.mcp_server.context import ToolContext
from src.mcp_server.tools import CATEGORY_MODULES
from tests.support.mcp_harness import FakeGate, tool_context

IDENTITY = "agent:dataworks-mcp"


def live_mcp() -> tuple[MCPServer, DataworksClient]:
    from src.main import fastapi_app

    client = DataworksClient("http://test", IDENTITY)
    client._http = httpx.AsyncClient(  # noqa: SLF001 - the transport is the only stand-in
        transport=httpx.ASGITransport(app=fastapi_app, raise_app_exceptions=False),
        base_url="http://test/api/v1",
        headers={AGENT_HEADER: IDENTITY},
    )
    mcp = MCPServer("live")
    ctx = ToolContext(settings=MCPSettings(auth_token="x" * 32), gate=FakeGate())  # type: ignore[arg-type]
    for modules in CATEGORY_MODULES.values():
        for module in modules:
            module.register(mcp, client, ctx)
    return mcp, client


def production_mcp(monkeypatch: Any) -> tuple[MCPServer, DataworksClient]:
    """The PRODUCTION build path (``server.build_server``, every default category): a tool that
    is defined but never registered there is absent here. Only the network is replaced: the
    health gate is open and the client's transport is the in-process app (ENH-006 G20, G22)."""
    from src.main import fastapi_app
    from src.mcp_server import server
    from src.mcp_server.config import DEFAULT_CATEGORIES

    monkeypatch.setattr(server, "HealthGate", lambda _url: FakeGate())
    mcp, client = server.build_server(
        MCPSettings(tool_categories=DEFAULT_CATEGORIES, auth_token="x" * 32)
    )
    client._http = httpx.AsyncClient(  # noqa: SLF001 - the transport is the only stand-in
        transport=httpx.ASGITransport(app=fastapi_app, raise_app_exceptions=False),
        base_url="http://test/api/v1",
        headers={AGENT_HEADER: IDENTITY},
    )
    return mcp, client


async def call(mcp: MCPServer, name: str, arguments: dict[str, Any]) -> Any:
    return await mcp._tool_manager.call_tool(name, arguments, tool_context(mcp))  # noqa: SLF001


def refusal_of(error: Exception) -> tuple[str, int, dict[str, Any]]:
    """``(code, status, details)`` of a tool's failure, read from the text an MCP caller receives:
    ``Error executing tool <name>: <code> (<status>): <message> <details as JSON>``."""
    text = str(error)
    found = re.search(r": ([A-Za-z_]+) \((\d{3})\): ", text)
    assert found is not None, text
    start = text.find(' {"', found.end())
    details = json.loads(text[start + 1 :]) if start >= 0 else {}
    return found.group(1), int(found.group(2)), details
