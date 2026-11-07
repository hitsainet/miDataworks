"""An agent told a name up to 200 long was refused by the route at 101 (dry run, 2026-11-01): the
tool must allow what the route allows, read from both, so the two cannot drift apart again."""

from __future__ import annotations

from src.schemas.recipes import CloneIn
from tests.support.mcp_harness import build_harness


def test_the_clone_tool_allows_the_names_the_route_allows() -> None:
    mcp, _ = build_harness()
    tools = mcp._tool_manager.list_tools()  # noqa: SLF001
    tool = next(t for t in tools if t.name == "dataworks_clone_recipe")
    limit = CloneIn.model_json_schema()["properties"]["name"]["maxLength"]
    assert tool.parameters["properties"]["name"]["maxLength"] == limit
