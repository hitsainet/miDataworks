"""FR-010.44: the tool inventory equals the agent-callable route set, checked LIVE.

Every ``(method, path)`` in the app's OpenAPI document has a tool that calls it (an
``EXPECTED_CALLS`` entry whose path matches the template) or a ``ROUTE_EXEMPT`` reason. The ledger
is ``tests/support/mcp_ledger.py``; its design record is 010 FTID section 3.8.

Three failures are kept apart, because each needs a different fix:

1. **A served route with no tool or exemption** - add the tool (or a reasoned exemption).
2. **A pending route that is now served** - its feature merged; build the tool and move the route
   out of ``PENDING_ROUTES``. A pending route does NOT fail the build while it stays unserved, and
   its tool must not register.
3. **An unexpected missing route** - a tool or exemption names a route nothing serves and nothing
   lists as pending; a route was renamed or removed under a tool.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.main import fastapi_app
from src.mcp_server.config import MCPSettings
from src.mcp_server.context import GATED_TOOLS
from src.mcp_server.server import build_server
from tests.support.mcp_ledger import (
    CALLER_ASSERTION_EXEMPT,
    EXPECTED_CALLS,
    LEDGER_TOOL_TOTAL,
    NOT_IN_SCHEMA,
    PENDING_ROUTES,
    PENDING_TOOLS_WITHOUT_ROUTE,
    ROUTE_EXEMPT,
    full,
    normalise,
    pending_tools,
    template_matches,
)

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def served_routes(app: Any = fastapi_app) -> dict[tuple[str, str], dict[str, Any]]:
    """``{(METHOD, normalised template): operation}`` from the live OpenAPI document.

    ``app.openapi()``, never ``app.routes``: FastAPI wraps included routers so route objects are
    unreliable for introspection (miStudio's notes record a phantom bug from it).
    """
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for path, operations in app.openapi()["paths"].items():
        for method, operation in operations.items():
            if method in HTTP_METHODS:
                out[(method.upper(), normalise(path))] = {**operation, "_template": path}
    return out


def tool_route(tool: str, served: dict[tuple[str, str], dict[str, Any]]) -> tuple[str, str] | None:
    method, path, _, _ = EXPECTED_CALLS[tool]
    concrete = full(path)
    matches = [
        key
        for key, operation in served.items()
        if key[0] == method and template_matches(operation["_template"], concrete)
    ]
    # A literal segment beats a parameter, as the router resolves it: /sources/meta, not
    # /sources/{source_id}.
    return min(matches, key=lambda key: key[1].count("{}"), default=None)


def registered_tools() -> set[str]:
    mcp, _ = build_server(MCPSettings(auth_token="x" * 32))
    return {t.name for t in asyncio.run(mcp.list_tools())}


@pytest.fixture(scope="module")
def served() -> dict[tuple[str, str], dict[str, Any]]:
    return served_routes()


# ---- 1. served routes are covered ------------------------------------------------------------


def uncovered(served: dict[tuple[str, str], dict[str, Any]]) -> set[tuple[str, str]]:
    called = {tool_route(t, served) for t in EXPECTED_CALLS}
    return {key for key in served if key not in called and key not in ROUTE_EXEMPT}


def test_every_served_route_has_a_tool_or_a_reasoned_exemption(served) -> None:
    missing = uncovered(served)
    assert not missing, (
        f"SERVED ROUTES WITH NO TOOL: {sorted(missing)}. Add a tool (and its EXPECTED_CALLS entry) "
        "or a ROUTE_EXEMPT reason in tests/support/mcp_ledger.py (FR-010.44)."
    )


def test_no_route_is_both_called_and_exempted(served) -> None:
    called = {tool_route(t, served) for t in EXPECTED_CALLS}
    both = called & set(ROUTE_EXEMPT)
    assert not both, f"routes both exempted and called by a tool: {sorted(both)}"


def test_every_exemption_has_a_reason() -> None:
    empty = [key for key, reason in ROUTE_EXEMPT.items() if len(reason.strip()) < 20]
    assert not empty, f"exemptions without a reason: {empty}"
    empty_pending = [
        key for key, (_, tools, reason) in PENDING_ROUTES.items() if not tools and not reason
    ]
    assert not empty_pending, f"pending routes with neither a tool nor a reason: {empty_pending}"


# ---- 2. pending routes -----------------------------------------------------------------------


def test_a_pending_route_that_is_now_served_fails_with_its_feature(served) -> None:
    arrived = {key: PENDING_ROUTES[key][0] for key in PENDING_ROUTES if key in served}
    assert not arrived, (
        f"PENDING ROUTES ARE NOW SERVED: {sorted(arrived.items())}. Their feature merged: build "
        "each route's tool (or move its exemption to ROUTE_EXEMPT) and remove it from "
        "PENDING_ROUTES."
    )


def test_no_pending_tool_is_registered() -> None:
    early = registered_tools() & set(pending_tools())
    assert not early, (
        f"tools registered before their route is served: {sorted(early)} (ADR-027). Each waits "
        "on its feature."
    )


def test_pending_routes_and_tools_are_disjoint_from_the_live_ledger() -> None:
    assert not set(pending_tools()) & set(EXPECTED_CALLS)
    assert not set(PENDING_ROUTES) & set(ROUTE_EXEMPT)


# ---- 3. unexpected missing routes ------------------------------------------------------------


def test_every_tool_calls_a_served_route(served) -> None:
    dangling = {t: EXPECTED_CALLS[t][:2] for t in EXPECTED_CALLS if tool_route(t, served) is None}
    assert not dangling, (
        f"UNEXPECTED MISSING ROUTES: these tools call routes nothing serves and nothing lists as "
        f"pending: {dangling}. A route was renamed or removed under its tool."
    )


def test_every_exemption_names_a_served_route(served) -> None:
    stale = sorted(key for key in ROUTE_EXEMPT if key not in served)
    assert not stale, (
        f"UNEXPECTED MISSING ROUTES: exemptions for routes that are not served: {stale}. Remove "
        "the stale exemption, or list the route as pending with its feature."
    )


def test_the_routes_outside_the_schema_are_still_outside_it(served) -> None:
    for path in NOT_IN_SCHEMA:
        assert not any(t == normalise(path) for _, t in served), path


# ---- accounting ------------------------------------------------------------------------------


def test_the_registry_equals_the_ledger() -> None:
    registered = registered_tools()
    accounted = set(EXPECTED_CALLS) | set(CALLER_ASSERTION_EXEMPT)
    assert registered == accounted, (
        f"registered but not in the ledger: {sorted(registered - accounted)}; in the ledger but "
        f"not registered: {sorted(accounted - registered)}"
    )


def test_registered_plus_pending_is_the_ledger_total() -> None:
    registered = registered_tools()
    assert len(registered) + len(pending_tools()) == LEDGER_TOOL_TOTAL, (
        len(registered),
        len(pending_tools()),
    )


def test_a_pending_tool_without_a_route_names_its_feature() -> None:
    for tool, (feature, reason) in PENDING_TOOLS_WITHOUT_ROUTE.items():
        assert feature and len(reason) > 20, tool


# ---- gating statements match the REST markers ------------------------------------------------


def test_gated_tools_match_the_live_x_approval_action_markers(served) -> None:
    """``GATED_TOOLS`` is a statement in descriptions and the contract; the gate is REST's.
    The statement must agree with the marker on the route each tool calls, both ways."""
    for tool in EXPECTED_CALLS:
        key = tool_route(tool, served)
        assert key is not None
        marker = served[key].get("x-approval-action")
        assert GATED_TOOLS.get(tool) == marker, (tool, GATED_TOOLS.get(tool), marker)
    stale = set(GATED_TOOLS) - set(EXPECTED_CALLS)
    assert not stale, f"GATED_TOOLS names tools that do not exist: {sorted(stale)}"


def test_every_gated_route_is_reached_by_a_gated_tool_or_is_exempt(served) -> None:
    called = {tool_route(t, served) for t in EXPECTED_CALLS}
    for key, operation in served.items():
        if operation.get("x-approval-action"):
            assert key in called or key in ROUTE_EXEMPT, key
