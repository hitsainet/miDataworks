"""Destructive is never under-claimed (022 FR-022.18; suite CHAIN_PLAN A13; 022-D21).

Each rule is derived from what a tool really does (its replayed calls, the gate its route carries,
the runtime keys it can write), never from the table being checked, and every rule is read off
the LIVE registry's published hints.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.core.runtime_settings import RUNTIME_KEYS
from src.mcp_server.config import MCPSettings
from src.mcp_server.context import GATED_TOOLS
from src.mcp_server.server import build_server
from tests.support.mcp_calls import replay, templated
from tests.support.mcp_ledger import EXPECTED_CALLS

SETTING_WRITE = ("PUT", "/api/v1/settings/{key}")
SETTING_DELETE = ("DELETE", "/api/v1/settings/{key}")


@pytest.fixture(scope="module")
def live() -> dict[str, Any]:
    mcp, _ = build_server(MCPSettings(auth_token="x" * 32))
    return {t.name: t for t in asyncio.run(mcp.list_tools())}


def recorded(tool: str) -> list[tuple[str, str]]:
    return [(m, templated(m, p)) for m, p, _ in replay(tool)]


def destructive(live: dict[str, Any], tool: str) -> bool:
    return bool(live[tool].annotations.destructive_hint)


def retention_keys() -> set[str]:
    """Runtime keys a daily prune acts on: lowering one deletes earlier (A13)."""
    keys = {
        k.key
        for k in RUNTIME_KEYS.values()
        if k.effect == "next daily prune" or "retention" in k.key
    }
    assert "agent_request_retention_days" in keys, "the retention key moved: re-read A13"
    return keys


def test_every_delete_call_is_destructive(live: dict[str, Any]) -> None:
    deleting = sorted(t for t in EXPECTED_CALLS if any(m == "DELETE" for m, _ in recorded(t)))
    assert len(deleting) >= 5, deleting
    for tool in deleting:
        assert destructive(live, tool), f"{tool} sends a DELETE and is not marked destructive"


def test_version_deletes_and_setting_deletes_are_destructive(live: dict[str, Any]) -> None:
    for tool, action in GATED_TOOLS.items():
        calls = recorded(tool)
        if action == "version_delete" or SETTING_DELETE in calls:
            assert destructive(live, tool), f"{tool} ({action}) deletes and is not destructive"


def test_cancels_and_clears_are_destructive(live: dict[str, Any]) -> None:
    named = sorted(t for t in live if t.startswith("dataworks_cancel_") or t.endswith("_clear"))
    assert {"dataworks_cancel_job", "dataworks_cancel_minimal_pair_chain"} <= set(named)
    for tool in named:
        assert destructive(live, tool), f"{tool} cancels or clears and is not destructive"


def test_a_setting_write_that_can_lower_a_retention_is_destructive(live: dict[str, Any]) -> None:
    """A tool that writes ``/settings/{key}`` with a free ``key`` can write any agent-writable
    runtime key, so its key set holds the retention key the daily prune acts on."""
    agent_writable = {k.key for k in RUNTIME_KEYS.values() if not k.agent_forbidden}
    found = 0
    for tool in EXPECTED_CALLS:
        if SETTING_WRITE not in recorded(tool):
            continue
        key_schema = live[tool].input_schema.get("properties", {}).get("key")
        if key_schema is None:
            # The key is fixed in the path (``dataworks_set_hf_token`` writes ``hf_token``).
            keys = {EXPECTED_CALLS[tool][1].rsplit("/", 1)[-1]}
        else:
            keys = set(key_schema.get("enum") or agent_writable)
        if keys & retention_keys():
            found += 1
            assert destructive(live, tool), f"{tool} can lower a retention and is not destructive"
    assert found >= 1, "no tool writes a retention key: the rule checked nothing"


def test_no_gated_tool_is_read_only(live: dict[str, Any]) -> None:
    """A tool that can answer a pending approval files a row the operator must handle (022-D5)."""
    for tool in GATED_TOOLS:
        assert tool in live, f"GATED_TOOLS names {tool}, which is not registered"
        assert not live[tool].annotations.read_only_hint, f"{tool} is gated and marked read-only"


#: Every destructive tool by name, with the call that makes it so and why (R3 review fix
#: (OR3-022-6), 2026-11-16). The kind count alone let one destructive tool be swapped for one
#: write elsewhere; this set must equal the live registry's, and each row's call must be in the
#: tool's replay, so a row cannot outlive the behaviour it names.
DESTRUCTIVE: dict[str, tuple[tuple[str, str], str]] = {
    "dataworks_cancel_job": (("POST", "/api/v1/jobs/{job_id}/cancel"), "cancels work in progress"),
    "dataworks_cancel_minimal_pair_chain": (
        ("POST", "/api/v1/minimal-pair-chains/{chain_id}/cancel"),
        "cancels work in progress",
    ),
    "dataworks_delete_setting": (SETTING_DELETE, "deletes a stored setting"),
    "dataworks_delete_source": (("DELETE", "/api/v1/sources/{source_id}"), "deletes a source"),
    "dataworks_delete_version": (("DELETE", "/api/v1/versions/{version_id}"), "deletes a version"),
    "dataworks_draw_audit": (
        ("POST", "/api/v1/versions/{version_id}/audit"),
        "supersedes an audit in progress",
    ),
    "dataworks_request_warning_level_clear": (
        ("DELETE", "/api/v1/datasets/{dataset_id}/shortcut-level"),
        "clears a warning level",
    ),
    "dataworks_reset_environment": (SETTING_DELETE, "deletes environment settings"),
    "dataworks_set_endpoint_key": (
        ("PUT", "/api/v1/endpoint-roles/{role}"),
        "overwrites a stored key, keeping no old value",
    ),
    "dataworks_set_environment": (SETTING_WRITE, "can lower a retention the daily prune acts on"),
    "dataworks_set_hf_token": (SETTING_WRITE, "overwrites a stored secret, keeping no old value"),
}


def test_the_destructive_tools_are_exactly_the_named_ones(live: dict[str, Any]) -> None:
    marked = {name for name in live if destructive(live, name)}
    assert marked == set(DESTRUCTIVE), {
        "marked, not named": sorted(marked - set(DESTRUCTIVE)),
        "named, not marked": sorted(set(DESTRUCTIVE) - marked),
    }


@pytest.mark.parametrize("tool", sorted(DESTRUCTIVE))
def test_each_named_destructive_tool_makes_its_call(live: dict[str, Any], tool: str) -> None:
    call, why = DESTRUCTIVE[tool]
    assert destructive(live, tool), f"{tool} {why} and is not destructive"
    assert call in recorded(tool), f"{tool}: {call} is not in its replay {recorded(tool)}"


def test_the_secret_overwrites_write_the_secret(live: dict[str, Any]) -> None:
    """The two secret overwrites, checked on their payload: ``hf_token`` in the path, and a key
    in the endpoint role's body."""
    token = [p for m, p, _ in replay("dataworks_set_hf_token") if m == "PUT"]
    assert token and all(p.rstrip("/").endswith("/settings/hf_token") for p in token), token
    keyed = [kw for m, _, kw in replay("dataworks_set_endpoint_key") if m == "PUT"]
    assert len(keyed) == 1 and "api_key" in (keyed[0].get("json_body") or {}), keyed
