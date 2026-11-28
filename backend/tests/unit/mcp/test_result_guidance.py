"""The guidance names the shared approval object, next_steps and the error layout (022 FR-022.10)."""

from __future__ import annotations

import asyncio

from src.mcp_server.config import MCPSettings
from src.mcp_server.server import _server_instructions, build_server


def test_the_instructions_name_the_approval_object_and_stay_short() -> None:
    text = _server_instructions()
    assert len(text) <= 4096, len(text)
    assert "`approval`" in text and "server_runs" in text and "next_steps" in text


def test_the_results_topic_describes_approval_next_steps_and_errors() -> None:
    mcp, _ = build_server(MCPSettings(auth_token="x" * 32))
    result = asyncio.run(mcp.call_tool("dataworks_howto", {"topic": "results"}))
    text = "\n".join(c.text for c in result.content)
    for needle in ("approval", "then", "server_runs", "next_steps", "unknown_arguments", "fix:"):
        assert needle in text, needle
