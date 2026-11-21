"""Every live tool carries a title and honest hints from ONE table (022 FR-022.15, .19; 022-D3).

Read from the LIVE registry (``build_server`` and ``list_tools()``), never from the table's module:
a table nothing applies would pass a test that reads the table.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.mcp_server import annotations as ann
from src.mcp_server.config import MCPSettings
from src.mcp_server.server import build_server, tool_names_declared
from src.mcp_server.tools import CATEGORY_MODULES


def live_tools() -> list[Any]:
    mcp, _ = build_server(MCPSettings(auth_token="x" * 32))
    return asyncio.run(mcp.list_tools())


@pytest.fixture(scope="module")
def tools() -> list[Any]:
    return live_tools()


def test_every_live_tool_is_annotated(tools: list[Any]) -> None:
    assert len(tools) >= 220, f"only {len(tools)} tools listed: the registry did not build"
    for tool in tools:
        assert tool.title, f"{tool.name} has no title"
        assert tool.title != tool.name, tool.name
        assert tool.annotations is not None, f"{tool.name} carries no annotations"
        assert tool.annotations.read_only_hint is not None, f"{tool.name}: readOnlyHint unset"
        assert tool.annotations.destructive_hint is not None, f"{tool.name}: destructiveHint unset"


def test_the_hints_are_the_tables(tools: list[Any]) -> None:
    for tool in tools:
        read_only, destructive = ann.HINTS[ann.TOOL_CLASS[tool.name]]
        assert tool.annotations.read_only_hint is read_only, tool.name
        assert tool.annotations.destructive_hint is destructive, tool.name
        assert not (read_only and destructive), tool.name


def test_no_other_hints_are_claimed(tools: list[Any]) -> None:
    """FR-022.19 (022-D7): adding one deliberately means changing this test and the FR."""
    for tool in tools:
        assert tool.annotations.idempotent_hint is None, tool.name
        assert tool.annotations.open_world_hint is None, tool.name


def test_titles_are_unique(tools: list[Any]) -> None:
    titles = [t.title for t in tools]
    duplicates = sorted({t for t in titles if titles.count(t) > 1})
    assert not duplicates, duplicates


def test_titles_read_as_words() -> None:
    assert ann.title_for("dataworks_get_split_download_url") == "Get split download URL"
    assert ann.title_for("dataworks_search_hf_datasets") == "Search HF datasets"
    assert ann.title_for("dataworks_list_miserver_probes") == "List miServer probes"
    assert ann.title_for("dataworks_howto") == "How-to"
    assert ann.title_for("dataworks_validate_trl") == "Validate TRL"


def test_the_published_wire_form_carries_both_hints(tools: list[Any]) -> None:
    """What a client reads: camelCase keys on the wire, both present."""
    wire = next(t for t in tools if t.name == "dataworks_list_datasets").model_dump(
        by_alias=True, exclude_none=True
    )
    assert wire["title"] == "List datasets"
    assert wire["annotations"] == {"readOnlyHint": True, "destructiveHint": False}


def test_rows_name_declared_tools() -> None:
    """A row naming a tool no module declares (AST) is a stale or misspelt row."""
    declared = {
        name
        for modules in CATEGORY_MODULES.values()
        for module in modules
        for name in tool_names_declared(module)
    }
    stale = sorted(set(ann.TOOL_CLASS) - declared)
    assert not stale, f"TOOL_CLASS rows for tools no module declares: {stale}"
    assert set(ann.TOOL_CLASS.values()) <= {"read", "write", "destructive"}


def test_a_tool_without_a_row_refuses_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """022-D3: the server will not start with an unclassified tool, and names it."""
    table = dict(ann.TOOL_CLASS)
    del table["dataworks_get_version"]
    monkeypatch.setattr(ann, "TOOL_CLASS", table)
    with pytest.raises(SystemExit) as refused:
        build_server(MCPSettings(auth_token="x" * 32))
    assert "dataworks_get_version" in str(refused.value)


def test_the_counts_are_recorded() -> None:
    """The 2026-11-16 classification (FTDD 022 section 4): a change of kind is deliberate.

    126 reads since 021 added ``dataworks_get_wsp_partitions`` (021 FTASKS 23.1, 2026-11-17).
    """
    kinds = list(ann.TOOL_CLASS.values())
    # Destructive is pinned tool by tool in test_destructive_claims.py (DESTRUCTIVE), not by a
    # count (R3 review fix (OR3-022-6), 2026-11-16).
    assert (kinds.count("read"), kinds.count("write")) == (126, 91)
