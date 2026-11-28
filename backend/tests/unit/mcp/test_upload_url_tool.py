"""``dataworks_get_upload_url`` (G-3, operator decision 2026-11-25) in the LIVE registry: its one
call, payload and call count, and the description an agent reads before its one ``curl -T``."""

from __future__ import annotations

from tests.support.mcp_harness import build_harness, call_tool

TOOL = "dataworks_get_upload_url"


def test_the_minimal_call_sends_only_what_was_given() -> None:
    mcp, client = build_harness()
    call_tool(mcp, TOOL, {"filename": "old_rows.jsonl", "expected_bytes": 24_000_000})
    assert client.calls == [
        (
            "POST",
            "/sources/upload-url",
            {
                "json_body": {
                    "filename": "old_rows.jsonl",
                    "split": "train",
                    "expected_bytes": 24_000_000,
                    "expires_in_s": 3600,
                }
            },
        )
    ]


def test_the_description_states_the_one_lan_put() -> None:
    mcp, _ = build_harness()
    tool = {t.name: t for t in mcp._tool_manager.list_tools()}[TOOL]  # noqa: SLF001
    text = tool.description or ""
    assert "curl -T" in text and "LAN" in text and "once" in text
    assert "no approval" in text.lower()


def test_the_upload_url_tool_is_not_gated() -> None:
    from src.mcp_server.context import GATED_TOOLS

    assert TOOL not in GATED_TOOLS  # operator decision 2026-11-25: no approval action
