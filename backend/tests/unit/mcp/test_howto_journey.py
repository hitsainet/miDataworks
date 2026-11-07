"""The ``behaviour_dataset`` howto topic and the status answer's journey (017 FTASKS 11.5;
FR-017.57; TD-017.38), checked against the LIVE registry.

The topic is static (the MCP server may not import backend code, ADR-016) and points at
``dataworks_get_behaviour_status``, whose answer carries the ordered journey from
``services/behaviour/journey.py``. Every tool the journey or the topic names is registered;
every approval a step names is a registered approval action; the topic is in the live tool's
schema enum. That neither names a behaviour is ``tests/unit/test_behaviour_names_absent.py``'s
guard (both live under ``backend/src``, and the topic is not one of its exempt topics).
"""

from __future__ import annotations

import asyncio
import re

from src.core.agent_origin import APPROVAL_ACTIONS
from src.mcp_server.config import MCPSettings
from src.mcp_server.server import build_server
from src.mcp_server.tools.core import HOWTO
from src.services.behaviour import journey


def registry() -> dict[str, dict]:  # type: ignore[type-arg]
    mcp, _ = build_server(MCPSettings(auth_token="x" * 32))
    return {t.name: t.input_schema for t in asyncio.run(mcp.list_tools())}


def test_every_tool_the_journey_names_is_registered() -> None:
    live = registry()
    named = journey.journey_tools()
    assert len(named) >= 20, named
    assert [t for t in named if t not in live] == []


def test_the_walk_names_only_registered_tools_and_actions() -> None:
    live = registry()
    walked = journey.walk("any-key", {})
    assert walked["next"]["tool"] == journey.JOURNEY[0].tool and walked["next"]["tool"] in live
    for step in walked["journey"]:
        assert step["tool"] in live and set(step["also"]) <= set(live), step
        assert set(step["approvals"]) <= set(APPROVAL_ACTIONS), step
    assert set(walked["approvals_ahead"]) <= set(APPROVAL_ACTIONS)


def test_the_topic_is_in_the_live_schema_and_names_registered_tools() -> None:
    live = registry()
    prop = live["dataworks_howto"]["properties"]["topic"]
    enum = [v for option in prop.get("anyOf", [prop]) for v in option.get("enum", [])]
    assert "behaviour_dataset" in enum, prop
    text = HOWTO["behaviour_dataset"]
    assert "dataworks_get_behaviour_status" in text
    named = set(re.findall(r"dataworks_[a-z_]+", text))
    assert named and named <= set(live), named - set(live)
