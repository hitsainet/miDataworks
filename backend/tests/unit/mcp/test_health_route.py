"""The MCP server's /health: categories, dependency states, no internal URL (FTASKS 6.6)."""

from __future__ import annotations

import httpx

from src.mcp_server.config import MCPSettings
from src.mcp_server.server import build_http_app, build_server


async def test_health_reports_categories_dependencies_and_unknown_names(monkeypatch) -> None:
    monkeypatch.setenv("DATAWORKS_API_URL", "http://midataworks-backend:8000")
    settings = MCPSettings(auth_token="x" * 32, tool_categories="core,datasets,bogus")
    mcp, _ = build_server(settings)
    # The production app (bearer-gated); /health is the one route it leaves open.
    transport = httpx.ASGITransport(app=build_http_app(mcp, settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://m") as client:
        response = await client.get("/health")
    body = response.json()
    assert response.status_code == 200
    assert body["categories"] == ["core", "datasets"]
    assert body["unknown_categories"] == ["bogus"]
    assert set(body["dependencies"]) == {"backend", "postgres", "redis", "data_volume"}
    assert "midataworks-backend" not in response.text


def test_the_instructions_count_equals_the_live_registry() -> None:
    import asyncio

    mcp, _ = build_server(MCPSettings(auth_token="x" * 32))
    count = len(asyncio.run(mcp.list_tools()))
    assert f"{count} tools across 11 categories" in (mcp.instructions or "")
