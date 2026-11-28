"""Tool errors teach: code first, then the fix, the next calls and the original text (022 FR-022.11 - .13).

Driven through ``mcp.call_tool`` of the PRODUCTION build (``build_server``), the path the SDK's
protocol handler takes, with the backend replaced by an ``httpx.MockTransport``. Every line 1 is
``<code>: <one-line message>`` with a lower-case code valid as the vendored contract's
``error_code``, and no text starts ``Error executing tool``.
"""

from __future__ import annotations

import ast
import asyncio
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from src.mcp_server import teaching
from src.mcp_server.config import MCPSettings
from src.mcp_server.health_gate import HealthGate
from src.mcp_server.server import build_server
from tests.support.result_language import schema, validate

LINE_ONE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)?: \S.{0,199}$")
SRC = Path(__file__).resolve().parents[3] / "src"


@pytest.fixture(autouse=True)
def _gate_open(monkeypatch: pytest.MonkeyPatch) -> None:
    async def unavailable(self: Any, *_: str) -> None:
        return None

    monkeypatch.setattr(HealthGate, "unavailable", unavailable)


def server(handler: Callable[[httpx.Request], httpx.Response]) -> Any:
    mcp, client = build_server(MCPSettings(auth_token="x" * 32))
    client._http = httpx.AsyncClient(  # noqa: SLF001
        base_url="http://backend/api/v1", transport=httpx.MockTransport(handler)
    )
    return mcp


def call(mcp: Any, tool: str, args: dict[str, Any]) -> list[str]:
    result = asyncio.run(mcp.call_tool(tool, args))
    assert result.is_error, result
    text: str = result.content[0].text
    assert not text.startswith("Error executing tool"), text
    lines = text.split("\n")
    assert LINE_ONE.match(lines[0]), lines[0]
    validate("error_code", lines[0].split(":", 1)[0])
    return lines


def refusal(status: int, code: str, message: str, details: dict[str, Any]) -> Callable[..., Any]:
    def handler(request: httpx.Request) -> httpx.Response:
        body = {"error": {"code": code, "message": message, "details": details}}
        return httpx.Response(status, json=body)

    return handler


NEXT = {"tool": "dataworks_list_versions", "args": {"dataset_id": "ds_1"}, "why": "find a version"}


def test_a_backend_refusal_reads_code_fix_next_then_the_original() -> None:
    details = {"version_id": "ver_1", "next": NEXT, "next_steps": [NEXT]}
    mcp = server(refusal(422, "UNPROCESSABLE", "The version is not ready.\nMore text.", details))
    lines = call(mcp, "dataworks_get_version", {"version_id": "ver_1"})
    assert lines[0] == "unprocessable: The version is not ready."
    assert lines[1] == "fix: find a version"
    assert lines[2].startswith("next: ") and json.loads(lines[2][6:]) == [NEXT]
    original = "\n".join(lines[3:])
    assert original.startswith("UNPROCESSABLE (422): The version is not ready."), original
    assert '"version_id": "ver_1"' in original, "the original text keeps the details"


def test_a_details_fix_wins_and_no_next_means_no_next_line() -> None:
    details = {"fix": "Build the version first."}
    mcp = server(refusal(409, "build_required", "No completed build.", details))
    lines = call(mcp, "dataworks_get_version", {"version_id": "ver_1"})
    assert lines[:2] == ["build_required: No completed build.", "fix: Build the version first."]
    assert not any(line.startswith("next: ") for line in lines)


def test_a_refusal_with_neither_is_code_then_the_original() -> None:
    mcp = server(refusal(404, "NOT_FOUND", "No version ver_1.", {}))
    lines = call(mcp, "dataworks_get_version", {"version_id": "ver_1"})
    assert lines[0] == "not_found: No version ver_1."
    assert lines[1].startswith("NOT_FOUND (404): No version ver_1.")


def test_an_unknown_argument_names_what_the_tool_accepts() -> None:
    mcp = server(refusal(500, "X", "never sent", {}))
    lines = call(mcp, "dataworks_get_version", {"version_id": "ver_1", "verison": 2})
    assert lines[0].startswith("unknown_arguments: ")
    assert lines[1].startswith("fix: ") and "version_id" in lines[1]
    assert "verison" in "\n".join(lines[2:]) and "Nothing was run" in "\n".join(lines)


def test_an_invalid_argument_names_the_field_only() -> None:
    mcp = server(refusal(500, "X", "never sent", {}))
    lines = call(mcp, "dataworks_get_version", {"version_id": "../../etc"})
    assert lines[0].startswith("invalid_arguments: ") and "version_id" in lines[0]


def test_an_unknown_tool() -> None:
    mcp = server(refusal(500, "X", "never sent", {}))
    lines = call(mcp, "dataworks_no_such_tool", {})
    assert lines[0] == "unknown_tool: Unknown tool: dataworks_no_such_tool"
    assert json.loads(lines[2][6:])[0]["tool"] == "dataworks_howto"


def test_an_unexpected_exception_is_internal_error_with_no_detail() -> None:
    mcp = server(refusal(500, "X", "never sent", {}))

    @mcp.tool()
    async def dataworks_crashes() -> None:
        raise RuntimeError("secret internals")

    lines = call(mcp, "dataworks_crashes", {})
    assert lines[0].startswith("internal_error: ")
    assert "secret internals" not in "\n".join(lines)


@pytest.mark.parametrize(
    ("code", "answer"),
    [
        ("backend_timeout", httpx.ReadTimeout("slow")),
        ("non_json_error", httpx.Response(502, text="<html>bad gateway</html>")),
        ("non_json_response", httpx.Response(200, text="<html>login</html>")),
    ],
)
def test_the_servers_own_codes_carry_a_fix_and_next(code: str, answer: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(answer, Exception):
            raise answer
        return answer

    lines = call(server(handler), "dataworks_get_version", {"version_id": "ver_1"})
    assert lines[0].startswith(f"{code}: ")
    assert lines[1].startswith("fix: ")
    steps = json.loads(lines[2][6:])
    assert steps and steps[-1]["tool"] == "dataworks_health"
    if code == "backend_timeout":
        assert [s["tool"] for s in steps] == ["dataworks_list_jobs", "dataworks_health"]
    for step in steps:
        validate("next_step", step)
    assert lines[3].startswith(code.upper() + " ("), "the original text keeps the code as raised"


def test_an_unreachable_backend_reads_the_same_way_where_it_is_raised() -> None:
    """Most tools answer an unreachable backend as ``{"unavailable": ...}`` (their gate); a call
    that raises it still teaches."""
    from mcp.server.mcpserver.exceptions import ToolError

    from src.mcp_server.client import DataworksError

    cause = DataworksError(0, "BACKEND_UNREACHABLE", "The backend could not be reached.")
    try:
        raise ToolError(f"Error executing tool dataworks_x: {cause}") from cause
    except ToolError as exc:
        text = teaching.teach("dataworks_x", exc).content[0].text
    lines = text.split("\n")
    assert lines[0] == "backend_unreachable: The backend could not be reached."
    assert json.loads(lines[2][6:])[0]["tool"] == "dataworks_health"


def test_the_own_fixes_name_live_tools() -> None:
    mcp, _ = build_server(MCPSettings(auth_token="x" * 32))
    live = {t.name for t in asyncio.run(mcp.list_tools())}
    for _, steps in teaching.OWN_FIXES.values():
        for tool, _, _ in steps:
            assert tool in live, tool


def test_a_secret_value_never_reaches_the_text() -> None:
    mcp = server(refusal(500, "X", "never sent", {}))
    result = asyncio.run(
        mcp.call_tool("dataworks_set_hf_token", {"token": "s3cret-value", "tokne": 1})
    )
    assert result.is_error
    assert "s3cret-value" not in result.content[0].text


def test_the_error_code_pattern_is_the_contracts() -> None:
    contract = schema()["$defs"]["error_code"]
    assert teaching.ERROR_CODE.pattern == contract["pattern"]
    assert teaching.ERROR_CODE_MAX == contract["maxLength"]


def test_line_one_code_lowers_and_falls_back() -> None:
    assert teaching.line_one_code("UNPROCESSABLE") == "unprocessable"
    assert teaching.line_one_code("rule-set.missing") == "rule_set.missing"
    assert teaching.line_one_code("9lives") == "error"
    assert teaching.line_one_code("a" * 65) == "error"


def code_literals() -> set[str]:
    """Every ``code="..."`` keyword and class-level ``code = "..."`` in backend/src."""
    found: set[str] = set()
    files = 0
    for path in SRC.rglob("*.py"):
        files += 1
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.keyword) and node.arg == "code":
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    found.add(node.value.value)
            if isinstance(node, ast.ClassDef):
                for stmt in node.body:
                    if (
                        isinstance(stmt, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == "code" for t in stmt.targets)
                        and isinstance(stmt.value, ast.Constant)
                        and isinstance(stmt.value.value, str)
                    ):
                        found.add(stmt.value.value)
    assert files > 300, files
    return found


def test_every_code_literal_normalises_without_the_fallback() -> None:
    codes = code_literals()
    assert len(codes) >= 250, f"only {len(codes)} code literals found: the scan is wrong"
    falls_back = sorted(c for c in codes if teaching.line_one_code(c) == "error" and c != "error")
    assert not falls_back, falls_back


def test_a_refusal_with_only_a_legacy_next_still_shows_it() -> None:
    """Review R2 finding 8: a backend that sends ``next`` without ``next_steps`` (an older
    backend) still teaches the next call."""
    mcp = server(refusal(404, "rule_set_not_found", "No stored rule set x.", {"next": NEXT}))
    lines = call(mcp, "dataworks_get_version", {"version_id": "ver_1"})
    assert lines[1] == "fix: find a version"
    assert json.loads(lines[2][6:]) == [NEXT]
