"""Bearer auth and the startup refusals (FR-010.3; FTASKS 6.3)."""

from __future__ import annotations

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from src.mcp_server.config import MCPSettings
from src.mcp_server.server import BearerAuthMiddleware, build_server

TOKEN = "t" * 40


def _app() -> Starlette:
    async def ok(_request):  # type: ignore[no-untyped-def]
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/mcp", ok, methods=["POST"]), Route("/health", ok)])
    app.add_middleware(BearerAuthMiddleware, token=TOKEN)
    return app


async def _post(headers: dict[str, str | bytes]) -> int:
    transport = httpx.ASGITransport(app=_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://m") as client:
        return (await client.post("/mcp", headers=headers)).status_code


async def test_a_missing_token_is_401() -> None:
    assert await _post({}) == 401


async def test_a_wrong_token_is_401() -> None:
    assert await _post({"Authorization": "Bearer wrong"}) == 401


async def test_a_non_ascii_token_is_401_not_500() -> None:
    assert await _post({"Authorization": "Bearer tökén".encode()}) == 401


async def test_the_right_token_passes() -> None:
    assert await _post({"Authorization": f"Bearer {TOKEN}"}) == 200


async def test_health_needs_no_token() -> None:
    transport = httpx.ASGITransport(app=_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://m") as client:
        assert (await client.get("/health")).status_code == 200


def test_http_start_without_a_token_is_refused() -> None:
    with pytest.raises(SystemExit, match="MCP_AUTH_TOKEN is required"):
        build_server(MCPSettings(auth_token=""), stdio=False)


def test_anonymous_over_http_is_refused_even_with_the_flag() -> None:
    with pytest.raises(SystemExit):
        build_server(MCPSettings(auth_token="", allow_anonymous=True), stdio=False)
    with pytest.raises(SystemExit, match="stdio transport only"):
        build_server(MCPSettings(auth_token=TOKEN, allow_anonymous=True), stdio=False)


def test_anonymous_on_stdio_is_allowed() -> None:
    mcp, _ = build_server(MCPSettings(auth_token="", allow_anonymous=True), stdio=True)
    assert mcp is not None


def test_stdio_without_a_token_needs_the_explicit_flag() -> None:
    with pytest.raises(SystemExit, match="MCP_ALLOW_ANONYMOUS"):
        build_server(MCPSettings(auth_token=""), stdio=True)


async def test_the_production_http_app_carries_the_bearer_check(monkeypatch) -> None:
    """The middleware __main__ adds is what stands between the LAN and the tools."""
    from src.mcp_server import __main__ as entry

    captured: dict[str, object] = {}

    def fake_run(app, **_kwargs):  # type: ignore[no-untyped-def]
        captured["app"] = app

    monkeypatch.setenv("MCP_AUTH_TOKEN", TOKEN)
    monkeypatch.setattr("uvicorn.run", fake_run)
    monkeypatch.setattr(entry.asyncio, "run", lambda coro: coro.close())
    assert entry.main([]) == 0
    transport = httpx.ASGITransport(app=captured["app"])
    async with httpx.AsyncClient(transport=transport, base_url="http://m") as client:
        assert (await client.post("/mcp", json={})).status_code == 401
        assert (await client.get("/health")).status_code == 200
