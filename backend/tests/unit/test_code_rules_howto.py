"""The ``code_rules`` howto topic (FR-018.35; FTASKS 11.5): every tool it names is in the LIVE
registry, and every tool of the category is named (an agent learns the whole loop from it)."""

from __future__ import annotations

import asyncio
import re

from src.mcp_server.config import DEFAULT_CATEGORIES, MCPSettings
from src.mcp_server.server import build_server
from src.mcp_server.tools.core import HOWTO


def _live() -> set[str]:
    mcp, _ = build_server(MCPSettings(tool_categories=DEFAULT_CATEGORIES, auth_token="x" * 32))
    return {t.name for t in asyncio.run(mcp.list_tools())}


def test_every_tool_the_topic_names_is_registered_and_the_category_is_covered() -> None:
    live = _live()
    named = set(re.findall(r"dataworks_[a-z_]+", HOWTO["code_rules"]))
    assert named <= live, sorted(named - live)
    category = {
        "dataworks_get_runner_contract",
        "dataworks_test_code_rule",
        "dataworks_save_code_rule",
        "dataworks_list_code_rules",
        "dataworks_get_code_rule",
        "dataworks_archive_code_rule",
        "dataworks_unarchive_code_rule",
    }
    assert category <= live
    assert category <= named, sorted(category - named)


def test_the_topic_names_no_behaviour_and_no_approving_tool() -> None:
    text = HOWTO["code_rules"]
    assert "no tool does" in text
    assert not re.search(r"dataworks_(revoke|approve)_code_rule", text)
