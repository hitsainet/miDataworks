"""Probe-verdict runs through MCP (009, operator decision 2026-10-07): an agent starts one with
``dataworks_start_label_run`` (role ``probe`` plus ``probe_id``), plans it with
``dataworks_plan_label_run`` and finds probes with ``dataworks_list_millm_probes``. Payload AND call
count asserted, through the MCP SDK's own argument validation."""

from __future__ import annotations

import json

from tests.support.mcp_harness import build_harness, call_tool


def test_start_sends_the_probe_and_nothing_a_probe_does_not_take() -> None:
    mcp, client = build_harness()
    call_tool(
        mcp,
        "dataworks_start_label_run",
        {
            "input_version_id": "ver_1",
            "role": "probe",
            "probe_id": "pr_humor",
            "window": "last_user",
            "field_map": {"text": "headline"},
        },
    )
    assert client.calls == [
        (
            "POST",
            "/label-runs",
            {
                "json_body": {
                    "input_version_id": "ver_1",
                    "role": "probe",
                    "probe": {"probe_id": "pr_humor", "window": "last_user"},
                    "field_map": {"text": "headline"},
                    "transport": "single",
                }
            },
        )
    ]


def test_the_window_is_left_to_the_server_default_when_not_given() -> None:
    mcp, client = build_harness()
    call_tool(
        mcp,
        "dataworks_plan_label_run",
        {"input_version_id": "ver_1", "role": "probe", "probe_id": "pr_humor",
         "field_map": {"text": "t"}},
    )  # fmt: skip
    [(method, path, payload)] = client.calls
    assert (method, path) == ("GET", "/label-runs/plan")
    assert json.loads(payload["request"])["probe"] == {"probe_id": "pr_humor"}


def test_a_classifier_start_carries_no_probe_key() -> None:
    mcp, client = build_harness()
    call_tool(
        mcp,
        "dataworks_start_label_run",
        {"input_version_id": "ver_1", "role": "classifier", "template_id": "dt_1"},
    )
    [(_, _, payload)] = client.calls
    assert "probe" not in payload["json_body"]


def test_the_probe_list_tool_reads_the_route() -> None:
    mcp, client = build_harness()
    call_tool(mcp, "dataworks_list_millm_probes", {})
    assert client.calls == [("GET", "/labeling/probes", {})]


def test_a_feature_tag_start_sends_the_sae_read() -> None:
    mcp, client = build_harness()
    call_tool(
        mcp,
        "dataworks_start_label_run",
        {
            "input_version_id": "ver_1",
            "role": "features",
            "sae_read": {"top_k": 8, "positions": "last"},
            "field_map": {"text": "headline"},
        },
    )
    assert client.calls == [
        (
            "POST",
            "/label-runs",
            {
                "json_body": {
                    "input_version_id": "ver_1",
                    "role": "features",
                    "features": {"top_k": 8, "positions": "last"},
                    "field_map": {"text": "headline"},
                    "transport": "single",
                }
            },
        )
    ]
