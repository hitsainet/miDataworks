"""``dataworks_get_version_manifest`` builds the publish files it needs (ENH-006 G29, 2026-11-06).

The manifest describes built files, so the route answers ``409 build_required`` for a version
whose files were never built; the D128 run read ``dataworks_get_build_manifest`` instead. The tool
now starts the build, waits for it and reads again, through the LIVE server and the real client
over a mock transport: every request's method, path, payload and the call count are asserted.
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
from src.mcp_server.tools import exports

MANIFEST = {"schema": "midataworks.dataset-version/v1", "version": {"id": "ver_1"}}
REQUIRED = {"error": {"code": "build_required", "message": "no completed build", "details": {}}}
STARTED = {"build_id": "pbld_1", "job_id": "job_1", "reused": False, "status": "queued"}


@pytest.fixture(autouse=True)
def _fast(monkeypatch: pytest.MonkeyPatch) -> None:
    async def unavailable(self: Any, *_: str) -> None:
        return None

    monkeypatch.setattr(HealthGate, "unavailable", unavailable)
    monkeypatch.setattr(exports, "BUILD_POLL_S", 0.0)


def _server(answers: list[tuple[str, int, Any]]) -> tuple[Any, list[httpx.Request]]:
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
    return asyncio.run(
        mcp.call_tool("dataworks_get_version_manifest", {"version_id": "ver_1", **args})
    )


def _structured(result: Any) -> Any:
    return json.loads(result.content[0].text)


def test_an_unbuilt_version_is_built_then_its_manifest_read() -> None:
    mcp, seen = _server(
        [
            ("GET", 409, REQUIRED),
            ("POST", 202, STARTED),
            ("GET", 200, {"id": "pbld_1", "status": "building"}),
            ("GET", 200, {"id": "pbld_1", "status": "completed"}),
            ("GET", 200, MANIFEST),
        ]
    )
    result = _call(mcp, label_column="probe_truth")
    assert [(r.method, r.url.path) for r in seen] == [
        ("GET", "/api/v1/versions/ver_1/handoff-manifest"),
        ("POST", "/api/v1/versions/ver_1/publish-builds"),
        ("GET", "/api/v1/publish-builds/pbld_1"),
        ("GET", "/api/v1/publish-builds/pbld_1"),
        ("GET", "/api/v1/versions/ver_1/handoff-manifest"),
    ]
    assert json.loads(seen[1].content) == {"label_column": "probe_truth"}
    assert _structured(result) == MANIFEST


def test_a_built_version_reads_once_and_starts_nothing() -> None:
    mcp, seen = _server([("GET", 200, MANIFEST)])
    assert _structured(_call(mcp)) == MANIFEST
    assert len(seen) == 1


def test_wait_zero_answers_build_required_without_building() -> None:
    mcp, seen = _server([("GET", 409, REQUIRED)])
    with pytest.raises(ToolError, match="build_required|no completed build"):
        _call(mcp, wait_for_build_s=0)
    assert len(seen) == 1


def test_a_failed_build_is_named_not_retried() -> None:
    mcp, seen = _server(
        [
            ("GET", 409, REQUIRED),
            ("POST", 202, STARTED),
            ("GET", 200, {"id": "pbld_1", "status": "failed"}),
        ]
    )
    with pytest.raises(ToolError, match="pbld_1"):
        _call(mcp)
    assert len(seen) == 3
