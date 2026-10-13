"""The production streamable-HTTP app, end to end over JSON-RPC (mcp SDK 2.x; Dependabot #8).

``test_auth.py`` proves the bearer middleware refuses; nothing drove the app ``__main__`` serves
with the RIGHT token through to a tool. Three mcp 2.x changes are invisible to every test that
calls the tool manager directly, and each one fails here:

* the app factory defaults ``host`` to ``127.0.0.1``, which turns on DNS-rebinding protection
  with a loopback-only ``Host`` allow-list: every request to ``midataworks-mcp:8765`` (the
  in-cluster service) would be ``421``;
* request bodies over 4 MiB are ``413`` by default, below a 10 MiB ``dataworks_upload_file``
  (about 13.3 MiB once base64);
* only a ``ToolError``'s message reaches the client; any other exception reads ``Error
  executing tool <name>``, which would hide every backend refusal and the "may already have
  been applied" timeout warning.

The app is taken from ``__main__.main`` itself (uvicorn stubbed), so this is the wiring that ships.
"""

from __future__ import annotations

import base64
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from src.mcp_server.client import DataworksClient, DataworksError
from src.mcp_server.health_gate import HealthGate
from src.mcp_server.server import MAX_REQUEST_BODY_BYTES
from src.mcp_server.tools.datasets import UPLOAD_MAX_BYTES

TOKEN = "t" * 40
#: The in-cluster service name and port (k8s/base/config.yaml MCP_INTERNAL_URL).
SERVICE = "http://midataworks-mcp:8765"
PROTOCOL = "2025-06-18"
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "MCP-Protocol-Version": PROTOCOL,
}


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The ASGI app ``__main__.main`` builds and would hand to uvicorn."""
    from src.mcp_server import __main__ as entry

    captured: dict[str, Any] = {}

    def fake_run(app: Any, **_kwargs: Any) -> None:
        captured["app"] = app

    monkeypatch.setenv("MCP_AUTH_TOKEN", TOKEN)
    monkeypatch.setenv("DATAWORKS_API_URL", "http://127.0.0.1:9")
    monkeypatch.setattr("uvicorn.run", fake_run)
    monkeypatch.setattr(entry.asyncio, "run", lambda coro: coro.close())
    assert entry.main([]) == 0
    return captured["app"]


@contextlib.asynccontextmanager
async def serving(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    """An HTTP client on ``app`` with its lifespan (the session manager) running.

    Entered inside each test, not in a fixture: anyio's cancel scope must be left in the task
    that entered it, and pytest-asyncio runs an async fixture's teardown in another.
    """
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=SERVICE) as http:
            yield http


async def _rpc(http: httpx.AsyncClient, method: str, params: dict[str, Any]) -> httpx.Response:
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    return await http.post("/mcp", headers=HEADERS, content=json.dumps(body))


def _backend_up(monkeypatch: pytest.MonkeyPatch) -> None:
    async def nothing_down(self: HealthGate, *deps: str) -> None:
        return None

    monkeypatch.setattr(HealthGate, "unavailable", nothing_down)


async def test_initialize_answers_json_statelessly_on_the_service_host(app) -> None:
    async with serving(app) as client:
        response = await _rpc(
            client,
            "initialize",
            {
                "protocolVersion": PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "1"},
            },
        )
        assert response.status_code == 200, response.text  # 421 if the Host allow-list were on
        assert response.headers["content-type"].startswith("application/json")  # json_response
        assert "mcp-session-id" not in response.headers  # stateless_http
        result = response.json()["result"]
        assert result["serverInfo"]["name"] == "midataworks"
        assert "Call `dataworks_howto` first." in result["instructions"]


async def test_tools_list_over_http_is_the_live_registry(app) -> None:
    async with serving(app) as client:
        from src.mcp_server.config import MCPSettings
        from src.mcp_server.server import build_server

        response = await _rpc(client, "tools/list", {})
        assert response.status_code == 200, response.text
        served = {t["name"] for t in response.json()["result"]["tools"]}
        mcp, _ = build_server(MCPSettings(auth_token=TOKEN))
        registry = {t.name for t in await mcp.list_tools()}
        assert served == registry
        assert len(served) >= 170


async def test_a_backend_refusal_reaches_the_agent_verbatim(app, monkeypatch) -> None:
    async with serving(app) as client:
        _backend_up(monkeypatch)
        calls: list[tuple[str, str]] = []

        async def refuse(self: DataworksClient, method: str, path: str, **_kw: Any) -> Any:
            calls.append((method, path))
            raise DataworksError(409, "VERSION_LOCKED", "ver_1 is being built", {"job": "job_9"})

        monkeypatch.setattr(DataworksClient, "request", refuse)
        response = await _rpc(
            client,
            "tools/call",
            {"name": "dataworks_get_version", "arguments": {"version_id": "ver_1"}},
        )
        assert response.status_code == 200, response.text
        result = response.json()["result"]
        assert result["isError"] is True
        text = result["content"][0]["text"]
        assert "VERSION_LOCKED (409): ver_1 is being built" in text
        assert '"job": "job_9"' in text
        assert calls == [("GET", "/versions/ver_1")]


async def test_a_timeout_still_warns_the_request_may_have_been_applied(app, monkeypatch) -> None:
    async with serving(app) as client:
        _backend_up(monkeypatch)

        async def timeout(self: httpx.AsyncHTTPTransport, request: httpx.Request) -> Any:
            raise httpx.ReadTimeout("slow", request=request)

        # The backend client's network transport, not the ASGI one this test speaks through.
        monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", timeout)
        response = await _rpc(
            client,
            "tools/call",
            {"name": "dataworks_get_version", "arguments": {"version_id": "ver_1"}},
        )
        result = response.json()["result"]
        assert result["isError"] is True
        assert "may already have been applied" in result["content"][0]["text"]


async def test_an_upload_at_the_cap_is_not_refused_by_the_body_limit(app, monkeypatch) -> None:
    async with serving(app) as client:
        _backend_up(monkeypatch)
        seen: list[int] = []

        async def upload(self: DataworksClient, *args: Any, **kwargs: Any) -> Any:
            seen.extend(
                len(content) for _field, (_name, content, _mime) in kwargs.get("files") or []
            )
            return {"id": "src_1"}

        monkeypatch.setattr(DataworksClient, "request", upload)
        payload = base64.b64encode(b"a" * UPLOAD_MAX_BYTES).decode()
        response = await _rpc(
            client,
            "tools/call",
            {
                "name": "dataworks_upload_file",
                "arguments": {"filename": "rows.jsonl", "content_base64": payload},
            },
        )
        assert response.status_code == 200, response.status_code  # 413 at mcp 2.x's 4 MiB default
        assert response.json()["result"]["isError"] is False, response.text[:500]
        assert seen == [UPLOAD_MAX_BYTES]


async def test_a_body_over_the_limit_is_413(app) -> None:
    async with serving(app) as client:
        body = (
            b'{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{"pad":"'
            + (b"a" * MAX_REQUEST_BODY_BYTES)
            + b'"}}'
        )
        response = await client.post("/mcp", headers=HEADERS, content=body)
        assert response.status_code == 413


async def test_the_right_token_is_still_required(app) -> None:
    async with serving(app) as client:
        headers = {k: v for k, v in HEADERS.items() if k != "Authorization"}
        response = await client.post("/mcp", headers=headers, content=b"{}")
        assert response.status_code == 401


async def test_an_upload_over_the_cap_is_refused_with_its_reason(app, monkeypatch) -> None:
    async with serving(app) as client:
        _backend_up(monkeypatch)
        calls: list[Any] = []

        async def never(self: DataworksClient, *args: Any, **kwargs: Any) -> Any:
            calls.append(args)
            return {}

        monkeypatch.setattr(DataworksClient, "request", never)
        # Over the decoded cap, still under the HTTP body limit: the TOOL refuses, by name.
        payload = "A" * ((UPLOAD_MAX_BYTES * 4) // 3 + 100)
        response = await _rpc(
            client,
            "tools/call",
            {
                "name": "dataworks_upload_file",
                "arguments": {"filename": "rows.jsonl", "content_base64": payload},
            },
        )
        assert response.status_code == 200
        result = response.json()["result"]
        assert result["isError"] is True
        assert "10 MiB MCP upload cap" in result["content"][0]["text"]
        assert calls == []
