"""Generated output schemas and structured results (022 FR-022.21 - .24).

- The committed ``output_schemas.json`` is what the generator writes from the live OpenAPI document.
- Every schema is permissive: a pending approval, an ``unavailable`` answer and any legacy ``next``
  (a string, an object or a list) conform, and no schema declares ``next``.
- Through the production build, a schema'd tool answers ``structuredContent`` with its text block
  unchanged; a non-dict value is ``{"result": value}``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import jsonschema
import pytest
from mcp.server.mcpserver.utilities.func_metadata import _convert_to_content

from scripts.generate_output_schemas import SCHEMAS_PATH, document_text
from src.mcp_server.config import MCPSettings
from src.mcp_server.health_gate import HealthGate
from src.mcp_server.server import as_one_block, build_server, load_output_schemas, shaped

PENDING = {
    "approval_id": "apr_1",
    "status": "pending",
    "action": "hub_push",
    "request_digest": "sha256:0",
    "expires_at": "2026-11-17T00:00:00+00:00",
    "hint": "Waiting for the operator to approve this in miDataworks.",
    "approval": {
        "id": "apr_1",
        "status": "pending",
        "decide_in": "midataworks",
        "then": "server_runs",
        "summary": "x",
    },
}
UNAVAILABLE = {"unavailable": "backend", "reason": "backend down: test"}
STEP = {"tool": "dataworks_list_jobs", "args": {}, "why": "see"}


@pytest.fixture(autouse=True)
def _gate_open(monkeypatch: pytest.MonkeyPatch) -> None:
    async def unavailable(self: Any, *_: str) -> None:
        return None

    monkeypatch.setattr(HealthGate, "unavailable", unavailable)


def test_the_committed_schemas_are_the_generated_ones() -> None:
    assert SCHEMAS_PATH.read_text(encoding="utf-8") == document_text(), (
        "output_schemas.json is stale or edited by hand: "
        "cd backend && python -m scripts.generate_output_schemas --write"
    )


def test_the_coverage_is_recorded() -> None:
    document = json.loads(SCHEMAS_PATH.read_text(encoding="utf-8"))
    assert len(document["tools"]) >= 120, len(document["tools"])
    for name, entry in document["untyped"].items():
        assert entry["reason"], name
    assert not set(document["tools"]) & set(document["untyped"])


@pytest.mark.parametrize("name", sorted(load_output_schemas()))
def test_every_schema_is_permissive_and_never_constrains_next(name: str) -> None:
    schema = load_output_schemas()[name]
    assert schema["type"] == "object" and "required" not in schema
    assert "next" not in schema["properties"], f"{name} constrains the legacy next"
    for body in (
        PENDING,
        UNAVAILABLE,
        {"next": "dataworks_list_jobs"},
        {"next": STEP, "next_steps": [STEP]},
        {"next": [STEP], "next_steps": [STEP]},
    ):
        jsonschema.validate(body, schema)


def test_a_non_dict_value_is_structured_as_result() -> None:
    done = shaped("done", structured=True)
    assert done.structured_content == {"result": "done"}
    nothing = shaped(None, structured=True)
    assert nothing.structured_content == {"result": None}
    assert nothing.content[0].text == "null"
    rows = shaped([{"a": 1}], structured=True)
    assert rows.structured_content == {"result": [{"a": 1}]}
    assert rows.content[0].text == as_one_block([{"a": 1}])
    assert shaped([], structured=False) == "[]"


def server(answer: Any) -> Any:
    mcp, client = build_server(MCPSettings(auth_token="x" * 32))
    client._http = httpx.AsyncClient(  # noqa: SLF001
        base_url="http://backend/api/v1",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=answer)),
    )
    return mcp


def test_a_schemad_tool_answers_structured_content_with_its_text_unchanged() -> None:
    body = {"id": "ver_1", "state": "completed", "next": "dataworks_get_build_manifest"}
    mcp = server(body)
    assert load_output_schemas().get("dataworks_get_version")
    result = asyncio.run(mcp.call_tool("dataworks_get_version", {"version_id": "ver_1"}))
    assert result.structured_content == body
    assert [c.text for c in result.content] == [c.text for c in _convert_to_content(body)]


def test_the_server_publishes_the_schemas() -> None:
    mcp, _ = build_server(MCPSettings(auth_token="x" * 32))
    listed = {t.name: t for t in asyncio.run(mcp.list_tools())}
    schemas = load_output_schemas()
    assert {n for n, t in listed.items() if t.output_schema} == set(schemas) & set(listed)
    assert listed["dataworks_get_version"].output_schema == schemas["dataworks_get_version"]


def test_a_tool_without_a_schema_answers_as_before() -> None:
    mcp = server([{"id": "rb_1"}])
    assert "dataworks_list_rubrics" not in load_output_schemas()
    result = asyncio.run(mcp.call_tool("dataworks_list_rubrics", {}))
    assert result.structured_content is None
    assert result.content[0].text == as_one_block([{"id": "rb_1"}])


def test_the_conformance_fixture_is_wired() -> None:
    from tests.unit.mcp import conftest

    before = len(conftest.VALIDATED)
    mcp = server({"id": "ver_1"})
    asyncio.run(mcp.call_tool("dataworks_get_version", {"version_id": "ver_1"}))
    assert len(conftest.VALIDATED) == before + 1


def test_the_audit_still_sees_a_pending_answer() -> None:
    """The audit wraps the shaped function; a structured pending answer still reads pending."""
    import logging

    from src.mcp_server.server import wrap_tool_with_audit

    mcp = server(PENDING)
    wrap_tool_with_audit(mcp)
    records: list[str] = []

    class Grab(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    handler = Grab()
    logger = logging.getLogger("src.mcp_server.server")
    logger.addHandler(handler)
    old = logger.level
    logger.setLevel(logging.INFO)
    try:
        args = {"version_id": "ver_1", "build_id": "bld_1", "repo_id": "owner/set"}
        assert "dataworks_publish_version" in load_output_schemas()
        asyncio.run(mcp.call_tool("dataworks_publish_version", args))
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old)
    audit = [r for r in records if "mcp_tool_call" in r]
    assert audit and "status=pending" in audit[0], audit


def test_the_generator_drops_a_routes_next_and_opens_its_result() -> None:
    """No response model declares ``next`` today, so the removal is pinned on a synthetic route."""
    from scripts.generate_output_schemas import schema_for

    components = {
        "Body": {
            "type": "object",
            "properties": {
                "next": {"$ref": "#/components/schemas/Step"},
                "result": {"type": "integer"},
                "id": {"type": "string"},
            },
            "required": ["id", "next"],
            "additionalProperties": False,
        },
        "Step": {
            "type": "object",
            "properties": {"tool": {"type": "string"}},
            "required": ["tool"],
        },
    }
    operation = {
        "responses": {
            "200": {
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Body"}}}
            }
        }
    }
    schema, reason = schema_for(operation, components)
    assert schema is not None, reason
    assert "next" not in schema["properties"]
    assert schema["properties"]["result"] == {}
    assert "required" not in json.dumps(schema)
    jsonschema.validate({"next": "anything", "result": [1]}, schema)


def test_a_stale_schema_file_refuses_the_start() -> None:
    """Review R2 finding 8: a schema naming a tool no module declares is a stale file."""
    from src.mcp_server.server import attach_output_schemas

    mcp, _ = build_server(MCPSettings(auth_token="x" * 32))
    with pytest.raises(SystemExit, match="dataworks_no_such_tool"):
        attach_output_schemas(mcp, {"dataworks_no_such_tool": {"type": "object"}})


@pytest.mark.parametrize(
    ("tool", "answer", "status"),
    [
        (
            "dataworks_get_version",
            {"unavailable": "backend", "reason": "down"},
            "status=unavailable",
        ),
        ("dataworks_preview_hf_dataset", {"unavailable": ["licence", "size"]}, "status=ok"),
    ],
)
def test_the_audit_reads_unavailable_through_a_shaped_result(
    tool: str, answer: Any, status: str
) -> None:
    """Review R2 findings 3 and 7: the unavailable shape names a dependency; a body's own
    ``unavailable`` list (the HF preview's missing facts) is not it."""
    import logging

    from src.mcp_server.server import wrap_tool_with_audit

    mcp = server(answer)
    wrap_tool_with_audit(mcp)
    records: list[str] = []

    class Grab(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    handler = Grab()
    logger = logging.getLogger("src.mcp_server.server")
    logger.addHandler(handler)
    old = logger.level
    logger.setLevel(logging.INFO)
    try:
        from tests.support.mcp_ledger import EXPECTED_CALLS

        asyncio.run(mcp.call_tool(tool, EXPECTED_CALLS[tool][2]))
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old)
    audit = [r for r in records if "mcp_tool_call" in r]
    assert audit and status in audit[0], audit


def test_a_route_with_two_bodies_by_status_is_untyped() -> None:
    """Review R2 finding 4: 200 SourceOut and 202 ImportAccepted are two answers."""
    from scripts.generate_output_schemas import schema_for

    components = {
        "A": {"type": "object", "properties": {"id": {"type": "string"}}},
        "B": {"type": "object", "properties": {"job_id": {"type": "string"}}},
    }

    def body(name: str) -> dict[str, Any]:
        return {
            "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{name}"}}}
        }

    schema, reason = schema_for({"responses": {"200": body("A"), "202": body("B")}}, components)
    assert schema is None and "several bodies" in reason
    same, _ = schema_for({"responses": {"200": body("A"), "201": body("A")}}, components)
    assert same is not None
