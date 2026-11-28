"""``dataworks_draw_audit`` forwards every field of the route's body (MCP gap G-6, FU-7a 2026-11-25).

The tool took only ``size`` and ``label_run_id``, while ``AuditDraw`` also accepts ``question``,
``seed`` and ``strata_columns``. So an agent could not audit an unlabelled version (the route
refuses ``AUDIT_NEEDS_QUESTION`` without a question) or draw a sample it could draw again. The
payload and the call count are asserted through the LIVE registry; the tool's limits are compared
with the route schema's own, so the two cannot drift apart.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.schemas.review import AuditDraw
from tests.support.mcp_harness import build_harness, call_tool

TOOL = "dataworks_draw_audit"


def draw(**arguments: Any) -> list[tuple[str, str, dict[str, Any]]]:
    mcp, client = build_harness()
    call_tool(mcp, TOOL, {"version_id": "v_unlabelled", **arguments})
    return client.calls


def test_an_unlabelled_version_can_be_audited_with_a_question_and_a_seed() -> None:
    calls = draw(size=50, question="Is this text everyday prose?", seed=2611256)
    assert calls == [
        (
            "POST",
            "/versions/v_unlabelled/audit",
            {
                "json_body": {
                    "size": 50,
                    "question": "Is this text everyday prose?",
                    "seed": 2611256,
                }
            },
        )
    ]


def test_strata_columns_and_a_label_run_are_forwarded() -> None:
    calls = draw(label_run_id="lr_1", strata_columns=["_dw_split", "genre"])
    assert calls == [
        (
            "POST",
            "/versions/v_unlabelled/audit",
            {
                "json_body": {
                    "size": 100,
                    "label_run_id": "lr_1",
                    "strata_columns": ["_dw_split", "genre"],
                }
            },
        )
    ]


def test_seed_zero_is_sent_not_dropped() -> None:
    (call,) = draw(seed=0)
    assert call[2]["json_body"] == {"size": 100, "seed": 0}


def test_the_tool_states_the_route_schemas_limits() -> None:
    mcp, _ = build_harness()
    tool = {t.name: t for t in mcp._tool_manager.list_tools()}[TOOL]  # noqa: SLF001
    props = tool.parameters["properties"]
    route = AuditDraw.model_json_schema()["properties"]

    def without_text(schema: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in schema.items() if k not in {"description", "title", "default"}}

    assert without_text(props["question"]) == without_text(route["question"])
    assert without_text(props["seed"]) == without_text(route["seed"])
    assert without_text(props["strata_columns"]) == without_text(route["strata_columns"])


@pytest.mark.parametrize(
    "arguments",
    [
        {"question": "q" * 4097},
        {"strata_columns": [f"c{i}" for i in range(9)]},
        {"strata_columns": [""]},
    ],
    ids=["question_too_long", "nine_strata", "empty_column"],
)
def test_out_of_limit_arguments_never_reach_the_route(arguments: dict[str, Any]) -> None:
    mcp, client = build_harness()
    with pytest.raises(Exception):  # noqa: B017, PT011 - the SDK's validation error
        call_tool(mcp, TOOL, {"version_id": "v1", **arguments})
    assert client.calls == []
