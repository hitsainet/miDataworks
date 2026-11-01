"""The audit line: one per call, the identity, secrets redacted before the digest (7.4)."""

from __future__ import annotations

import ast
import asyncio
import logging
from pathlib import Path

import pytest
from mcp.server.mcpserver import MCPServer

from src.mcp_server.server import AuditToolLogger, wrap_tool_with_audit
from tests.support.mcp_harness import PENDING_BODY, build_harness, tool_context

MAIN = Path(__file__).resolve().parents[3] / "src/mcp_server/__main__.py"


def _audited(response: object = None) -> tuple[MCPServer, object]:
    mcp, client = build_harness(response=response)
    mcp.dataworks_identity = "agent:dataworks-mcp"  # type: ignore[attr-defined]
    assert wrap_tool_with_audit(mcp) == len(mcp._tool_manager.list_tools())  # noqa: SLF001
    return mcp, client


def _lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("mcp_tool_call")]


def test_one_line_per_call_with_the_identity(caplog: pytest.LogCaptureFixture) -> None:
    mcp, _ = _audited()
    with caplog.at_level(logging.INFO, logger="src.mcp_server.server"):
        asyncio.run(
            mcp._tool_manager.call_tool(
                "dataworks_get_version", {"version_id": "ver_1"}, tool_context(mcp)
            )
        )  # noqa: SLF001
    (line,) = _lines(caplog)
    assert "tool=dataworks_get_version" in line
    assert "identity=agent:dataworks-mcp" in line
    assert "status=ok" in line


def test_a_pending_result_is_logged_as_pending(caplog: pytest.LogCaptureFixture) -> None:
    mcp, _ = _audited(response=dict(PENDING_BODY))
    with caplog.at_level(logging.INFO, logger="src.mcp_server.server"):
        asyncio.run(
            mcp._tool_manager.call_tool(  # noqa: SLF001
                "dataworks_delete_version",
                {"version_id": "ver_1", "reason": "r"},
                tool_context(mcp),
            )
        )
    assert "status=pending" in _lines(caplog)[0]


def test_secret_values_do_not_change_the_digest() -> None:
    a = AuditToolLogger.digest("dataworks_set_hf_token", {"token": "hf_one"})
    b = AuditToolLogger.digest("dataworks_set_hf_token", {"token": "hf_two"})
    assert a == b
    # Control: a non-secret argument does change it.
    c = AuditToolLogger.digest("dataworks_get_version", {"version_id": "ver_1"})
    d = AuditToolLogger.digest("dataworks_get_version", {"version_id": "ver_2"})
    assert c != d


def test_the_token_never_reaches_a_log(caplog: pytest.LogCaptureFixture) -> None:
    secret = "hf_SUPERSECRET_value_123"
    mcp, client = _audited()
    with caplog.at_level(logging.DEBUG):
        asyncio.run(
            mcp._tool_manager.call_tool(
                "dataworks_set_hf_token", {"token": secret}, tool_context(mcp)
            )
        )  # noqa: SLF001
    assert client.calls[0][2] == {"json_body": {"value": secret}}  # the payload does carry it
    assert secret not in caplog.text


def test_the_entry_point_wraps_the_tools_by_ast() -> None:
    """``__main__`` calls ``wrap_tool_with_audit``: without it no call is audited at all."""
    tree = ast.parse(MAIN.read_text())
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "wrap_tool_with_audit"
    ]
    assert len(calls) == 1


def test_the_sdk_logger_is_quietened() -> None:
    import src.mcp_server.__main__  # noqa: F401

    assert logging.getLogger("mcp").level == logging.WARNING
