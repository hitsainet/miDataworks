"""The list rule (017 FR-017.53, FTASKS 11.1; FTID 017 §5.3), read from the LIVE registry.

Every ``dataworks_list_*`` tool the server registers takes ``page`` and ``limit`` and hands both to
the backend; every list over a table that carries a ``behaviour_key`` column takes
``behaviour_key`` and hands it on. Nothing here is a hand-kept list of list tools: the tools come
from ``build_server``'s registry, the keyed tables from the SQLAlchemy metadata, and the call each
tool makes from the recording client. The one map kept by hand (keyed table -> its list tool) is
checked both ways, so a new keyed table without a filtered list turns this red.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.core.database import Base
from src.mcp_server.config import MCPSettings
from src.mcp_server.server import build_server
from src.schemas.behaviour import BEHAVIOUR_KEY_PATTERN
from tests.support.mcp_harness import build_harness, call_tool
from tests.support.mcp_ledger import EXPECTED_CALLS

#: Each table that carries a behaviour key, and the list tool over it.
KEYED_LISTS = {
    "dw_recipes": "dataworks_list_recipes",
    "dw_generation_templates": "dataworks_list_generation_templates",
    "dw_generation_runs": "dataworks_list_generation_runs",
    "dw_label_runs": "dataworks_list_label_runs",
    "dw_review_queues": "dataworks_list_review_queues",
    "dw_detector_sets": "dataworks_list_detector_sets",
    "dw_rubrics": "dataworks_list_rubrics",
    "dw_rule_sets": "dataworks_list_rule_sets",
    "dw_code_rules": "dataworks_list_code_rules",
}
#: A keyed table with no list of its own, and why.
NOT_LISTED = {
    "dw_rule_set_uses": "which build used which rule set: read on the rule set "
    "(dataworks_get_rule_set's uses), never listed on its own",
}


def live_schemas() -> dict[str, dict[str, Any]]:
    import src.models  # noqa: F401 - every table on the metadata

    mcp, _ = build_server(MCPSettings(auth_token="x" * 32))
    return {t.name: t.input_schema for t in asyncio.run(mcp.list_tools())}


@pytest.fixture(scope="module")
def schemas() -> dict[str, dict[str, Any]]:
    return live_schemas()


def list_tools(schemas: dict[str, dict[str, Any]]) -> list[str]:
    return sorted(name for name in schemas if name.startswith("dataworks_list_"))


def required_args(tool: str) -> dict[str, Any]:
    """The tool's documented call's arguments (the ledger), less the ones this test sets."""
    _, _, kwargs, _ = EXPECTED_CALLS[tool]
    return {k: v for k, v in kwargs.items() if k not in ("page", "limit", "behaviour_key")}


def test_the_registry_has_the_list_tools(schemas: dict[str, dict[str, Any]]) -> None:
    assert len(list_tools(schemas)) >= 31, list_tools(schemas)


def test_every_list_tool_takes_page_and_limit(schemas: dict[str, dict[str, Any]]) -> None:
    missing = {
        name: sorted({"page", "limit"} - set(schemas[name].get("properties", {})))
        for name in list_tools(schemas)
    }
    assert {k: v for k, v in missing.items() if v} == {}


@pytest.mark.parametrize("tool", list_tools(live_schemas()))
def test_every_list_tool_hands_page_and_limit_to_the_backend(tool: str) -> None:
    mcp, client = build_harness()
    call_tool(mcp, tool, {**required_args(tool), "page": 3, "limit": 7})
    _, _, payload = client.calls[-1]
    assert (payload.get("page"), payload.get("limit")) == (3, 7), (tool, payload)


def test_every_keyed_table_has_a_filtered_list() -> None:
    import src.models  # noqa: F401

    keyed = {t.name for t in Base.metadata.tables.values() if "behaviour_key" in t.c}
    assert keyed == set(KEYED_LISTS) | set(NOT_LISTED), keyed ^ (set(KEYED_LISTS) | set(NOT_LISTED))


@pytest.mark.parametrize("table", sorted(KEYED_LISTS))
def test_a_keyed_list_takes_and_hands_on_the_behaviour_key(
    table: str, schemas: dict[str, dict[str, Any]]
) -> None:
    tool = KEYED_LISTS[table]
    prop = schemas[tool]["properties"].get("behaviour_key")
    assert prop is not None, f"{tool} has no behaviour_key"
    patterns = [prop.get("pattern")] + [a.get("pattern") for a in prop.get("anyOf", [])]
    assert BEHAVIOUR_KEY_PATTERN in patterns, prop
    mcp, client = build_harness()
    call_tool(mcp, tool, {**required_args(tool), "behaviour_key": "fx-probe-key"})
    assert len(client.calls) == 1, client.calls
    _, _, payload = client.calls[0]
    assert payload.get("behaviour_key") == "fx-probe-key", (tool, payload)
