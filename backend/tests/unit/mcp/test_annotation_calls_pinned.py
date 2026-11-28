"""The ``calls`` of ``docs/mcp-annotations.json`` are the calls each tool really makes (022 FR-022.26).

The generator reads calls from the tools' code (every branch). This test replays every tool itself,
independently of the generator, with its ledger arguments and, for a tool with more than one call,
under each ``BRANCH_FIXTURES`` script that drives its other branches. Every recorded call, templated
against the live OpenAPI document by a matcher written apart from the generator's, must be in the
tool's published ``calls``; the replays together must cover every published entry; every published
path must be a live route carrying that method (R1A-19, R2A-6).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from scripts.generate_mcp_call_map import ANNOTATIONS_PATH
from src.mcp_server.client import DataworksError
from src.mcp_server.tools import detector_sets, exports
from tests.support.mcp_calls import ScriptedClient, openapi_paths, replay, templated
from tests.support.mcp_harness import build_harness, call_tool
from tests.support.mcp_ledger import CALLER_ASSERTION_EXEMPT, EXPECTED_CALLS

_PENDING_BUILDS = DataworksError(
    409, "builds_pending", "publish files are building", {"builds": [{"build_id": "pbld_1"}]}
)
_COMPLETED = {"build_id": "pbld_1", "status": "completed"}

#: tool -> the scripted answers that drive the branches its ledger replay does not reach.
BRANCH_FIXTURES: dict[str, list[tuple[str, str, Any]]] = {
    # No publish files yet: the manifest read answers build_required, the tool starts the build,
    # reads it until it completes, and reads the manifest again (ENH-006 G29).
    "dataworks_get_version_manifest": [
        ("GET", "/versions/ver_1/handoff-manifest", DataworksError(409, "build_required", "x")),
        ("POST", "/versions/ver_1/publish-builds", {"build_id": "pbld_1", "status": "queued"}),
        ("GET", "/publish-builds/pbld_1", _COMPLETED),
    ],
    # The send first answers builds_pending; the tool reads those builds, then sends again.
    "dataworks_send_detector_set": [
        ("POST", "/detector-sets/dts_1/send", _PENDING_BUILDS),
        ("GET", "/publish-builds/pbld_1", _COMPLETED),
    ],
    "dataworks_send_detector_sets": [
        ("POST", "/detector-sets/send", _PENDING_BUILDS),
        ("GET", "/publish-builds/pbld_1", _COMPLETED),
    ],
}


@pytest.fixture(autouse=True)
def _no_poll_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(exports, "BUILD_POLL_S", 0.0)
    monkeypatch.setattr(detector_sets, "BUILD_POLL_S", 0.0)


def published() -> dict[str, Any]:
    tools: dict[str, Any] = json.loads(ANNOTATIONS_PATH.read_text(encoding="utf-8"))["tools"]
    return tools


def branch_replay(tool: str) -> list[tuple[str, str, dict[str, Any]]]:
    client = ScriptedClient(BRANCH_FIXTURES[tool])
    mcp, _ = build_harness(client=client)
    call_tool(mcp, tool, EXPECTED_CALLS[tool][2])
    assert not client.script, f"{tool}: the branch script was not consumed: {client.script}"
    return client.calls


def as_set(calls: list[tuple[str, str, dict[str, Any]]]) -> set[tuple[str, str]]:
    return {(method, templated(method, path)) for method, path, _ in calls}


def test_every_replayed_call_is_published_and_every_entry_is_reached() -> None:
    table = published()
    replayed = 0
    for tool, entry in table.items():
        if tool in CALLER_ASSERTION_EXEMPT:
            assert entry["calls"] == [], f"{tool} makes no call and publishes {entry['calls']}"
            continue
        declared = {(c["method"], c["path"]) for c in entry["calls"]}
        seen = as_set(replay(tool))
        replayed += 1
        assert seen <= declared, f"{tool} called {sorted(seen - declared)}, not in its calls"
        if tool in BRANCH_FIXTURES:
            branch = as_set(branch_replay(tool))
            assert branch <= declared, f"{tool} branch called {sorted(branch - declared)}"
            seen |= branch
        assert seen == declared, f"{tool}: published calls never reached: {sorted(declared - seen)}"
    assert replayed >= 220, f"only {replayed} tools replayed"


def test_every_published_path_is_a_live_route_with_that_method() -> None:
    paths = openapi_paths()
    for tool, entry in published().items():
        for call in entry["calls"]:
            assert call["method"].lower() in paths.get(call["path"], {}), (tool, call)


def test_the_branch_fixtures_name_multi_call_tools() -> None:
    """A row for a tool with one call is dead weight that hides a renamed tool."""
    table = published()
    for tool in BRANCH_FIXTURES:
        assert len(table[tool]["calls"]) > 1, tool
