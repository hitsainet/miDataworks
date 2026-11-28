"""A read-only tool is proved by the calls it makes, not by its name (022 FR-022.17; 022-D14).

Each tool the LIVE registry marks ``readOnlyHint`` is replayed with its ledger arguments through
the recording client; every request it sends must be a ``GET`` or a route in
``READ_ONLY_ROUTES``, each of which ``tests/integration/test_read_only_routes_write_nothing.py``
proves writes nothing.
"""

from __future__ import annotations

import asyncio

import pytest

from src.mcp_server.config import MCPSettings
from src.mcp_server.server import build_server
from tests.support.mcp_calls import READ_ONLY_ROUTES, openapi_paths, replay, templated
from tests.support.mcp_ledger import CALLER_ASSERTION_EXEMPT, EXPECTED_CALLS


@pytest.fixture(scope="module")
def read_only_tools() -> list[str]:
    mcp, _ = build_server(MCPSettings(auth_token="x" * 32))
    return sorted(
        t.name
        for t in asyncio.run(mcp.list_tools())
        if t.annotations is not None and t.annotations.read_only_hint
    )


def test_every_read_only_tool_only_reads(read_only_tools: list[str]) -> None:
    replayed = 0
    offenders: list[str] = []
    for tool in read_only_tools:
        if tool in CALLER_ASSERTION_EXEMPT:
            continue
        calls = replay(tool)
        assert calls, f"{tool} is marked read-only and its replay made no call"
        replayed += 1
        for method, path, _ in calls:
            if method == "GET":
                continue
            if (method, templated(method, path)) not in READ_ONLY_ROUTES:
                offenders.append(f"{tool}: {method} {path}")
    assert replayed >= 120, f"only {replayed} read-only tools replayed"
    assert not offenders, f"read-only tools that write: {offenders}"


def test_every_read_only_route_is_served_and_called() -> None:
    """A stale entry (a route no read tool calls, or one not served) is a claim nothing checks."""
    paths = openapi_paths()
    for method, template in READ_ONLY_ROUTES:
        assert method.lower() in paths.get(template, {}), f"{method} {template} is not served"
    called = {(m, templated(m, p)) for tool, (m, p, _, _) in EXPECTED_CALLS.items() if m != "GET"}
    assert set(READ_ONLY_ROUTES) <= called, sorted(set(READ_ONLY_ROUTES) - called)


def test_the_non_read_tools_are_not_hidden_reads(read_only_tools: list[str]) -> None:
    """A tool that only GETs and is NOT marked read-only must say why (FTDD 022 section 4)."""
    from src.mcp_server.annotations import TOOL_CLASS

    write_but_only_gets = sorted(
        tool
        for tool, (method, _, _, _) in EXPECTED_CALLS.items()
        if tool not in read_only_tools
        and all(m == "GET" for m, _, _ in replay(tool))
        and method == "GET"
    )
    assert write_but_only_gets == ["dataworks_get_version_manifest"], (
        write_but_only_gets,
        "a GET-only replay of a write tool: its other branch writes (the manifest builds files)",
    )
    assert TOOL_CLASS["dataworks_get_version_manifest"] == "write"
