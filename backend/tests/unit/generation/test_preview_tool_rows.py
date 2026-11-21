"""The MCP preview tool forwards the seed-row fields (D1, 2026-10-08), through the registered tool."""

from __future__ import annotations

from tests.support.mcp_harness import build_harness, call_tool


def test_the_preview_tool_sends_seed_row_fields_once() -> None:
    mcp, client = build_harness()
    call_tool(
        mcp,
        "dataworks_preview_generation",
        {
            "input_version_id": "v1",
            "prompt_column": "prompt",
            "seed_splits": ["train"],
            "sample_size": 2,
            "respond_template_id": "gt_x",
            "seed": 4,
        },
    )
    assert len(client.calls) == 1
    method, path, payload = client.calls[0]
    assert (method, path) == ("POST", "/generation-runs/preview")
    assert payload == {
        "json_body": {
            "input_version_id": "v1",
            "prompt_column": "prompt",
            "seed_splits": ["train"],
            "sample_size": 2,
            "respond_template_id": "gt_x",
            "seed": 4,
        }
    }
