"""``dataworks_upload_file`` reads the operator's upload limit on every call (2026-10-20).

The integration test (tests/integration/test_mcp_upload_limit.py) drives the real backend; these
pin the edges with the recording client: the exact reads and calls, the refusal before sending,
the per-call read, and what happens when the backend serves no usable number.
"""

from __future__ import annotations

import base64
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from src.mcp_server.tools.datasets import TRANSPORT_MAX_BYTES, upload_limit
from tests.support.mcp_harness import build_harness, call_tool

FILE = b'{"text": "hello"}\n' * 10
META = ("GET", "/sources/meta", {})


def _args(content: bytes = FILE) -> dict[str, Any]:
    return {"filename": "a.jsonl", "content_base64": base64.b64encode(content).decode()}


def _meta(limit: object) -> dict[str, Any]:
    return {"limits": {"upload_max_bytes": limit, "import_confirm_bytes": 1}}


def test_a_file_over_the_operators_limit_is_refused_before_it_is_sent() -> None:
    mcp, client = build_harness(response=_meta(len(FILE) - 1))
    with pytest.raises(ToolError, match=rf"over the operator's {len(FILE) - 1:,}-byte upload"):
        call_tool(mcp, "dataworks_upload_file", _args())
    assert client.calls == [META]


def test_a_file_at_the_limit_is_sent() -> None:
    mcp, client = build_harness(response=_meta(len(FILE)))
    call_tool(mcp, "dataworks_upload_file", _args())
    assert [c[:2] for c in client.calls] == [("GET", "/sources/meta"), ("POST", "/sources/uploads")]
    _field, (_name, sent, _mime) = client.calls[1][2]["files"][0]
    assert sent == FILE


def test_the_limit_is_read_on_every_call() -> None:
    mcp, client = build_harness(response=_meta(len(FILE)))
    call_tool(mcp, "dataworks_upload_file", _args())
    client.response = _meta(len(FILE) - 1)
    with pytest.raises(ToolError):
        call_tool(mcp, "dataworks_upload_file", _args())
    assert [c[:2] for c in client.calls] == [
        ("GET", "/sources/meta"),
        ("POST", "/sources/uploads"),
        ("GET", "/sources/meta"),
    ]


@pytest.mark.parametrize(
    "meta", [{}, {"limits": {}}, _meta(None), _meta("big"), _meta(0), _meta(True)]
)
def test_no_usable_limit_leaves_the_refusal_to_the_backend(meta: dict[str, Any]) -> None:
    """The backend enforces the same limit on the upload itself, so the file is sent."""
    assert upload_limit(meta) is None
    mcp, client = build_harness(response=meta)
    call_tool(mcp, "dataworks_upload_file", _args())
    assert [c[:2] for c in client.calls] == [("GET", "/sources/meta"), ("POST", "/sources/uploads")]


def test_the_transport_ceiling_refuses_with_no_call_even_when_the_limit_is_higher() -> None:
    mcp, client = build_harness(response=_meta(2 * 1024**3))
    with pytest.raises(ToolError, match="the most one MCP request carries"):
        call_tool(mcp, "dataworks_upload_file", _args(b"a" * (TRANSPORT_MAX_BYTES + 1)))
    assert client.calls == []
