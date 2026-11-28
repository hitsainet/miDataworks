"""The MCP layer passes the suite's ``approval`` object through untouched (022 FR-022.6).

``client.py`` returns a 202 body as it came; a pass that rebuilt the pending answer from known keys
would drop ``approval``. The body here is the one the backend's gate builds (its shape is pinned by
``tests/integration/test_approval_object.py``) and validates against the vendored contract.
"""

from __future__ import annotations

from tests.support.mcp_harness import build_harness, call_tool
from tests.support.mcp_ledger import EXPECTED_CALLS
from tests.support.result_language import validate

PENDING_202 = {
    "approval_id": "apr_022",
    "status": "pending",
    "action": "hub_push",
    "request_digest": "sha256:" + "0" * 64,
    "expires_at": "2026-11-17T08:00:00+00:00",
    "hint": "Waiting for the operator to approve this in miDataworks.",
    "approval": {
        "id": "apr_022",
        "status": "pending",
        "decide_in": "midataworks",
        "then": "server_runs",
        "summary": "Publish ver_1 to org/data",
    },
}


def test_the_publish_tool_returns_the_approval_intact() -> None:
    validate("approval", PENDING_202["approval"])
    mcp, client = build_harness(response=PENDING_202)
    result = call_tool(
        mcp, "dataworks_publish_version", EXPECTED_CALLS["dataworks_publish_version"][2]
    )
    assert result == PENDING_202
    assert client.calls and client.calls[-1][0] == "POST"
