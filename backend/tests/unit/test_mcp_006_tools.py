"""006's MCP tools in the LIVE registry: agents may accept or flag only (P-10), and the target
write is the one gated tool (S3-08)."""

from __future__ import annotations

from src.mcp_server.context import GATED_TOOLS
from tests.support.mcp_harness import build_harness

TOOLS_006 = (
    "dataworks_import_calibration_set",
    "dataworks_build_calibration_set",
    "dataworks_list_calibration_sets",
    "dataworks_get_calibration_set",
    "dataworks_compute_calibration",
    "dataworks_list_calibration_records",
    "dataworks_get_calibration",
    "dataworks_get_gate_verdict",
    "dataworks_get_calibration_targets",
    "dataworks_set_calibration_target",
    "dataworks_preview_calibration_mapping",
    "dataworks_list_review_queues",
    "dataworks_create_review_queue",
    "dataworks_get_review_queue",
    "dataworks_get_review_rows",
    "dataworks_review_decide",
    "dataworks_get_review_decisions",
    "dataworks_draw_audit",
    "dataworks_get_audit_status",
)


def tools() -> dict[str, object]:
    mcp, _ = build_harness()
    return {t.name: t for t in mcp._tool_manager.list_tools()}  # noqa: SLF001


def test_review_decide_offers_accept_and_flag_only() -> None:
    schema = tools()["dataworks_review_decide"].parameters  # type: ignore[attr-defined]
    assert schema["properties"]["decision"]["enum"] == ["accept", "flag"]
    assert "override_label" not in schema["properties"]


def test_no_tool_can_create_an_external_queue_or_show_hidden_output() -> None:
    schema = tools()["dataworks_create_review_queue"].parameters  # type: ignore[attr-defined]
    assert schema["properties"]["kind"]["enum"] == ["label_review", "calibration_labeling"]
    assert "show_model_output" not in schema["properties"]


def test_only_the_target_write_is_gated_among_006_tools() -> None:
    names = set(TOOLS_006) & set(tools())
    assert {n: GATED_TOOLS[n] for n in names if n in GATED_TOOLS} == {
        "dataworks_set_calibration_target": "gate_target_write"
    }
    assert len(names) == 19
