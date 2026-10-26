"""``dataworks_howto`` and the tool descriptions (FR-010.12, .20, .21; FTASKS 9.5)."""

from __future__ import annotations

import pytest

from src.mcp_server.context import GATED_TOOLS
from src.mcp_server.tools.core import HOWTO
from tests.support.mcp_harness import build_harness, call_tool


def _tools() -> dict[str, object]:
    mcp, _ = build_harness()
    return {t.name: t for t in mcp._tool_manager.list_tools()}  # noqa: SLF001


@pytest.mark.parametrize("topic", sorted(HOWTO))
def test_every_topic_resolves(topic: str) -> None:
    mcp, client = build_harness()
    result = call_tool(mcp, "dataworks_howto", {"topic": topic})
    assert "text" in str(result) and client.calls == []


def test_howto_names_only_real_tools() -> None:
    import re

    names = set(_tools())
    mentioned = {m for text in HOWTO.values() for m in re.findall(r"dataworks_[a-z_]+", text)}
    assert mentioned <= names, sorted(mentioned - names)


def test_every_tool_and_parameter_has_a_description() -> None:
    for name, tool in _tools().items():
        assert (tool.description or "").strip(), f"{name} has no description"
        for param, schema in tool.parameters.get("properties", {}).items():
            assert schema.get("description"), f"{name}.{param} has no description"


def test_every_gated_tool_says_it_waits_for_the_operator() -> None:
    tools = _tools()
    for name in GATED_TOOLS:
        text = tools[name].description or ""
        params = " ".join(
            str(s.get("description", "")) for s in tools[name].parameters["properties"].values()
        )
        assert "approval" in (text + params).lower(), name


def test_no_tool_changes_the_allowlist_or_a_warning_level() -> None:
    """P-09: agents read the allowlist and warning levels; they never change them."""
    import re

    # Word-anchored: feature 004's read tool dataworks_get_dataSET_WARNING_level is a read.
    write = re.compile(r"(^|_)(allowlist_add|revoke|set_warning|set_shortcut|clear_warning)")
    for name in _tools():
        assert not write.search(name), name
