"""Converting a tool error keeps its log line (022 FR-022.32; R1A-11), and the audit still sees it
(FR-022.14).

The SDK's protocol handler logs each failure before converting it; ``DataworksMCPServer.call_tool``
returns a result instead, so it logs first, with the SDK's three branches: a crash with its
traceback (``ERROR`` with ``exc_info``), an argument refusal by field names only, any other failure
by its text (``INFO``). Argument values never reach the log.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
import pytest

from src.mcp_server.config import MCPSettings
from src.mcp_server.health_gate import HealthGate
from src.mcp_server.server import build_server, wrap_tool_with_audit

LOGGER = "mcp.server.mcpserver.server"


@pytest.fixture(autouse=True)
def _gate_open(monkeypatch: pytest.MonkeyPatch) -> None:
    async def unavailable(self: Any, *_: str) -> None:
        return None

    monkeypatch.setattr(HealthGate, "unavailable", unavailable)


def refusing_server() -> Any:
    mcp, client = build_server(MCPSettings(auth_token="x" * 32))

    def handler(request: httpx.Request) -> httpx.Response:
        body = {"error": {"code": "NOT_FOUND", "message": "No version ver_1.", "details": {}}}
        return httpx.Response(404, json=body)

    client._http = httpx.AsyncClient(  # noqa: SLF001
        base_url="http://backend/api/v1", transport=httpx.MockTransport(handler)
    )
    return mcp


def test_a_crash_is_logged_with_its_traceback(caplog: pytest.LogCaptureFixture) -> None:
    mcp = refusing_server()

    @mcp.tool()
    async def dataworks_crashes() -> None:
        raise RuntimeError("boom")

    with caplog.at_level(logging.INFO, logger=LOGGER):
        result = asyncio.run(mcp.call_tool("dataworks_crashes", {}))
    assert result.is_error
    errors = [r for r in caplog.records if r.name == LOGGER and r.levelno == logging.ERROR]
    assert len(errors) == 1, caplog.text
    assert errors[0].exc_info is not None
    assert "RuntimeError" in caplog.text and "boom" in caplog.text


def test_an_argument_refusal_logs_field_names_never_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    mcp = refusing_server()
    with caplog.at_level(logging.INFO, logger=LOGGER):
        result = asyncio.run(
            mcp.call_tool("dataworks_set_hf_token", {"token": "s3cret-value", "tokne": 1})
        )
    assert result.is_error
    assert "s3cret-value" not in caplog.text
    lines = [r.getMessage() for r in caplog.records if r.name == LOGGER]
    assert any("rejected arguments" in line for line in lines), lines


def test_a_backend_refusal_logs_one_info_line(caplog: pytest.LogCaptureFixture) -> None:
    mcp = refusing_server()
    with caplog.at_level(logging.INFO, logger=LOGGER):
        result = asyncio.run(mcp.call_tool("dataworks_get_version", {"version_id": "ver_1"}))
    assert result.is_error
    lines = [r for r in caplog.records if r.name == LOGGER]
    assert len(lines) == 1 and lines[0].levelno == logging.INFO, [r.getMessage() for r in lines]
    assert "NOT_FOUND" in lines[0].getMessage()


def test_the_audit_line_still_records_a_refused_call(caplog: pytest.LogCaptureFixture) -> None:
    """FR-022.14: the audit wraps the tool function, inside the conversion."""
    mcp = refusing_server()
    wrap_tool_with_audit(mcp)
    with caplog.at_level(logging.INFO, logger="src.mcp_server.server"):
        result = asyncio.run(mcp.call_tool("dataworks_get_version", {"version_id": "ver_1"}))
    assert result.is_error
    audit = [r.getMessage() for r in caplog.records if "mcp_tool_call" in r.getMessage()]
    assert len(audit) == 1 and "status=error:DataworksError" in audit[0], audit


def test_the_validation_branch_logs_locations_even_without_hidden_input(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Every production argument model hides input in its errors; the log line must not rely on
    that. A tool registered after the strict pass keeps the SDK's model, whose error text DOES
    carry the value: the log names the field only."""
    mcp = refusing_server()

    @mcp.tool()
    async def dataworks_takes_a_number(n: int) -> int:
        return n

    with caplog.at_level(logging.INFO, logger=LOGGER):
        result = asyncio.run(mcp.call_tool("dataworks_takes_a_number", {"n": "s3cret-value"}))
    assert result.is_error
    assert "s3cret-value" not in caplog.text
    assert any("['n']" in r.getMessage() for r in caplog.records if r.name == LOGGER), caplog.text
