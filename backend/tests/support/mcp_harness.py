# Origin (shape): miStudio (Onegaishimas/miStudio) backend/tests/unit/test_reachability.py
# @ c829a2cc — RecordingClient and the caller harness, adapted (010 FTID section 8.1).
"""The caller harness for MCP tools: one recording client, EVERY module in the registry.

miStudio's ``_tools_by_name`` registered a hand-kept subset of modules, so a documented call for a
tool in an unlisted module failed as "Unknown tool" only by luck. This harness iterates
``CATEGORY_MODULES`` itself.

The recording client's method signatures must EQUAL ``DataworksClient``'s
(``tests/unit/mcp/test_client.py`` compares them with ``inspect.signature``): miStudio's recorder
once gave ``put`` an optional body, and a tool calling ``put(path)`` passed every test and raised
``TypeError`` in production. A stand-in must never be more forgiving than the thing it replaces.
"""

from __future__ import annotations

import asyncio
from typing import Any

from mcp.server.mcpserver import Context, MCPServer

from src.mcp_server.config import MCPSettings
from src.mcp_server.context import ToolContext
from src.mcp_server.tools import CATEGORY_MODULES

PENDING_BODY = {
    "approval_id": "apr_test",
    "status": "pending",
    "action": "hub_push",
    "request_digest": "sha256:0",
    "expires_at": "2026-10-08T00:00:00+00:00",
}


class RecordingClient:
    """Records ``(method, path, kwargs)`` for every call a tool issues."""

    def __init__(self, response: Any = None) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.response = {"ok": True} if response is None else response

    async def _record(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append((method, path, kwargs))
        return self.response

    async def get(self, path: str, **params: Any) -> Any:
        return await self._record("GET", path, **params)

    async def post(self, path: str, json_body: Any = None, **params: Any) -> Any:
        return await self._record("POST", path, json_body=json_body, **params)

    async def put(self, path: str, json_body: Any) -> Any:
        return await self._record("PUT", path, json_body=json_body)

    async def patch(self, path: str, json_body: Any) -> Any:
        return await self._record("PATCH", path, json_body=json_body)

    async def delete(self, path: str, json_body: Any = None, **params: Any) -> Any:
        return await self._record("DELETE", path, json_body=json_body, **params)

    async def post_multipart(
        self,
        path: str,
        *,
        files: list[tuple[str, tuple[str, bytes, str]]],
        data: dict[str, str],
    ) -> Any:
        return await self._record("POST", path, files=files, data=data)


class FakeGate:
    """A health gate that is open, or closed on one dependency."""

    def __init__(self, closed: str | None = None) -> None:
        self.closed = closed
        self.invalidated = 0

    async def unavailable(self, *dependencies: str) -> dict[str, str] | None:
        if self.closed is not None and self.closed in dependencies:
            return {"unavailable": self.closed, "reason": f"{self.closed} down: test"}
        return None

    def invalidate(self) -> None:
        self.invalidated += 1


def build_harness(
    gate: FakeGate | None = None, response: Any = None
) -> tuple[MCPServer, RecordingClient]:
    """Register EVERY module of every category against one recording client."""
    mcp = MCPServer("harness")
    client = RecordingClient(response)
    ctx = ToolContext(settings=MCPSettings(auth_token="x" * 32), gate=gate or FakeGate())  # type: ignore[arg-type]
    for modules in CATEGORY_MODULES.values():
        for module in modules:
            module.register(mcp, client, ctx)  # type: ignore[arg-type]
    return mcp, client


def call_tool(mcp: MCPServer, name: str, arguments: dict[str, Any]) -> Any:
    """Call a tool through MCPServer's own argument validation and return the raw result.

    mcp 2.x's tool manager takes the request context positionally; a context with no active
    request is what ``MCPServer.call_tool`` builds itself when called outside one.
    """
    return asyncio.run(
        mcp._tool_manager.call_tool(name, arguments, tool_context(mcp))
    )  # noqa: SLF001


def tool_context(mcp: MCPServer) -> Context:  # type: ignore[type-arg]
    """A request-less ``Context`` for calling the tool manager directly (mcp 2.x)."""
    return Context(mcp_server=mcp)
