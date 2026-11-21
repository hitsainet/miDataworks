"""``dataworks_send_detector_set`` waits for its own publish builds (PII run L4, 2026-10-20).

The run got ``409 builds_pending`` (the send had just STARTED the builds), polled
``dataworks_get_publish_build`` itself and sent again. The tool now does that, through the LIVE
server and the real client over a mock transport: the payload and the call count of every
request are asserted, and the approval is still the route's (the second POST is the same
request through the same ``hub_push`` gate).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from src.mcp_server.config import MCPSettings
from src.mcp_server.health_gate import HealthGate
from src.mcp_server.server import build_server
from src.mcp_server.tools import detector_sets

PENDING = {"approval_id": "apr_1", "status": "pending", "action": "hub_push"}
BUSY = {
    "error": {
        "code": "builds_pending",
        "message": "being written",
        "details": {"builds": [{"version_id": "v1", "build_id": "pbld_1", "status": "queued"}]},
    }
}


@pytest.fixture(autouse=True)
def _fast(monkeypatch: pytest.MonkeyPatch) -> None:
    async def unavailable(self: Any, *_: str) -> None:
        return None

    monkeypatch.setattr(HealthGate, "unavailable", unavailable)
    monkeypatch.setattr(detector_sets, "BUILD_POLL_S", 0.0)


def _server(answers: list[tuple[str, int, Any]]) -> tuple[Any, list[httpx.Request]]:
    """``answers``: (method, status, body), consumed in order; a request out of order fails."""
    mcp, client = build_server(MCPSettings(auth_token="x" * 32))
    seen: list[httpx.Request] = []
    queue = list(answers)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        method, status, body = queue.pop(0)
        assert request.method == method, (request.method, request.url.path)
        return httpx.Response(status, json=body)

    client._http = httpx.AsyncClient(  # noqa: SLF001
        base_url="http://backend/api/v1",
        transport=httpx.MockTransport(handler),
        headers={"X-Dataworks-Agent": client.identity},
    )
    return mcp, seen


def _call(mcp: Any, **args: Any) -> Any:
    return asyncio.run(mcp.call_tool("dataworks_send_detector_set", {"set_id": "dts_1", **args}))


def _calls(seen: list[httpx.Request]) -> list[tuple[str, str]]:
    return [(r.method, r.url.path) for r in seen]


def test_the_send_waits_for_its_builds_then_files_the_approval() -> None:
    mcp, seen = _server(
        [
            ("POST", 409, BUSY),
            ("GET", 200, {"id": "pbld_1", "status": "building"}),
            ("GET", 200, {"id": "pbld_1", "status": "completed"}),
            ("POST", 202, PENDING),
        ]
    )
    result = _call(mcp, namespace="mistudio")
    assert json.loads(result.content[0].text) == PENDING
    assert _calls(seen) == [
        ("POST", "/api/v1/detector-sets/dts_1/send"),
        ("GET", "/api/v1/publish-builds/pbld_1"),
        ("GET", "/api/v1/publish-builds/pbld_1"),
        ("POST", "/api/v1/detector-sets/dts_1/send"),
    ]
    bodies = [json.loads(r.content) for r in seen if r.method == "POST"]
    assert (
        bodies[0]
        == bodies[1]
        == {
            "repositories": {},
            "namespace": "mistudio",
            "visibility": "private",
        }
    )
    assert all(r.headers["X-Dataworks-Agent"] == "agent:dataworks-mcp" for r in seen)


def test_a_failed_build_is_named_and_not_resent() -> None:
    mcp, seen = _server([("POST", 409, BUSY), ("GET", 200, {"id": "pbld_1", "status": "failed"})])
    with pytest.raises(ToolError, match="publish_build_failed"):
        _call(mcp)
    assert [m for m, _ in _calls(seen)] == ["POST", "GET"]


def test_wait_zero_answers_builds_pending_at_once() -> None:
    mcp, seen = _server([("POST", 409, BUSY)])
    with pytest.raises(ToolError, match="builds_pending"):
        _call(mcp, wait_for_builds_s=0)
    assert len(seen) == 1


def test_another_refusal_is_raised_unchanged() -> None:
    refused = {"error": {"code": "send_refused", "message": "D-4", "details": {}}}
    mcp, seen = _server([("POST", 409, refused)])
    with pytest.raises(ToolError, match="send_refused"):
        _call(mcp)
    assert len(seen) == 1


def test_builds_still_running_at_the_deadline_say_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(detector_sets, "BUILD_POLL_S", 1.1)
    building = ("GET", 200, {"id": "pbld_1", "status": "building"})
    mcp, seen = _server([("POST", 409, BUSY), building, building])
    with pytest.raises(ToolError, match="still running after 1 s"):
        _call(mcp, wait_for_builds_s=1)
    assert [m for m, _ in _calls(seen)] == ["POST", "GET", "GET"]
