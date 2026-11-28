"""The ``rule_signals`` howto topic (FR-016.36): every tool it names is in the LIVE registry."""

from __future__ import annotations

import asyncio
import re

from src.mcp_server.config import DEFAULT_CATEGORIES, MCPSettings
from src.mcp_server.server import build_server
from src.mcp_server.tools.core import HOWTO


def test_every_tool_the_topic_names_is_registered() -> None:
    mcp, _ = build_server(MCPSettings(tool_categories=DEFAULT_CATEGORIES, auth_token="x" * 32))
    live = {t.name for t in asyncio.run(mcp.list_tools())}
    named = set(re.findall(r"dataworks_[a-z_]+", HOWTO["rule_signals"]))
    assert len(named) >= 10, named
    assert named <= live, sorted(named - live)
