"""The MCP upload follows the operator's upload limit, read from the real backend (2026-10-20).

``dataworks_upload_file`` used a hard-coded 10 MiB of its own, separate from the operator's
``upload_max_bytes`` (Settings → Environment): it advertised and accepted 10 MiB whatever the
operator had set, and sent every such file to the backend to be refused there. Now the tool reads
the limit in force from ``GET /sources/meta`` (the value the backend enforces, one source) on
every call, and refuses over it before sending. 10 MiB remains only the transport ceiling.

Driven through the production tool against the real FastAPI app: the operator changes the
setting through the REST API, and the next MCP upload follows it.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from src.mcp_server.client import AGENT_HEADER, DataworksClient
from src.mcp_server.config import MCPSettings
from src.mcp_server.context import ToolContext
from src.mcp_server.tools import datasets
from tests.support.mcp_harness import FakeGate, tool_context
from tests.support.source_fixtures import HfEnv, hf_env, sources_where

__all__ = ["hf_env"]

IDENTITY = "agent:dataworks-mcp"
JSONL = "\n".join(
    json.dumps({"text": f"a line long enough to count {i}", "label": i % 2}) for i in range(9)
).encode()


async def _mcp_on_the_real_backend(
    sent: list[tuple[str, str]],
) -> tuple[MCPServer, DataworksClient]:
    """The production tool and client, over the in-process app; ``sent`` records each request."""
    from src.main import fastapi_app

    async def record(request: httpx.Request) -> None:
        sent.append((request.method, request.url.path))

    backend = DataworksClient("http://test", IDENTITY)
    await backend._http.aclose()  # noqa: SLF001 — the same client, over the in-process app
    backend._http = httpx.AsyncClient(  # noqa: SLF001
        transport=httpx.ASGITransport(app=fastapi_app, raise_app_exceptions=False),
        base_url="http://test/api/v1",
        headers={AGENT_HEADER: IDENTITY},
        event_hooks={"request": [record]},
    )
    mcp = MCPServer("upload-limit")
    ctx = ToolContext(settings=MCPSettings(auth_token="x" * 32), gate=FakeGate())  # type: ignore[arg-type]
    datasets.register(mcp, backend, ctx)  # type: ignore[arg-type]
    return mcp, backend


async def _upload(mcp: MCPServer) -> object:
    import base64

    arguments = {
        "filename": "rows.jsonl",
        "content_base64": base64.b64encode(JSONL).decode(),
    }
    return await mcp._tool_manager.call_tool(  # noqa: SLF001
        "dataworks_upload_file", arguments, tool_context(mcp)
    )


async def test_the_mcp_upload_follows_the_operators_limit(
    client: httpx.AsyncClient, hf_env: HfEnv, data_dir: Path, operator_name: str
) -> None:
    sent: list[tuple[str, str]] = []
    mcp, backend = await _mcp_on_the_real_backend(sent)
    try:
        limit = len(JSONL) - 1
        put = await client.put("/api/v1/settings/upload_max_bytes", json={"value": str(limit)})
        assert put.status_code == 200, put.text

        with pytest.raises(ToolError) as refused:
            await _upload(mcp)
        message = str(refused.value)
        assert "upload_too_large" in message
        assert f"{limit:,}-byte upload limit" in message
        # Refused BEFORE sending: the limit was read, the file never left the MCP server.
        assert sent == [("GET", "/api/v1/sources/meta")], sent
        assert sources_where() == [], "a refused upload created a source"

        # The operator raises the limit; the very next call follows it (read per call).
        put = await client.put("/api/v1/settings/upload_max_bytes", json={"value": str(len(JSONL))})
        assert put.status_code == 200, put.text
        sent.clear()
        accepted = await _upload(mcp)
        assert isinstance(accepted, dict) and "job_id" in accepted, accepted
        assert sent == [("GET", "/api/v1/sources/meta"), ("POST", "/api/v1/sources/uploads")]
    finally:
        await backend.close()
