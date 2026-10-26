"""The MCP tools over the REAL backend, in process (017: "read back through MCP").

Every category module is registered against a real ``DataworksClient`` whose HTTP client is swapped
for one on ``httpx.ASGITransport`` over the FastAPI app: a tool's call goes through the client's
own request code, the agent header, the route, the service and the database, and the answer comes
back through the tool. Only the network is replaced (the swap ``tests/unit/mcp/test_client.py``
makes). Requests carry ``agent:dataworks-mcp``: gated actions wait for approval as they would.
"""

from __future__ import annotations

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


async def call(mcp: MCPServer, name: str, arguments: dict[str, Any]) -> Any:
    return await mcp._tool_manager.call_tool(name, arguments, tool_context(mcp))  # noqa: SLF001
