"""DataworksClient: the header on every verb, the envelope, the non-JSON guard (FTASKS 7.2)."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import httpx
import pytest

from src.mcp_server.client import AGENT_HEADER, DataworksClient, DataworksError
from src.mcp_server.config import AGENT_IDENTITY_PATTERN
from tests.support.mcp_harness import RecordingClient

SRC = Path(__file__).resolve().parents[3] / "src"
IDENTITY = "agent:dataworks-mcp"


def _client(handler) -> tuple[DataworksClient, list[httpx.Request]]:  # type: ignore[no-untyped-def]
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = DataworksClient("http://backend", IDENTITY)
    client._http = httpx.AsyncClient(  # noqa: SLF001 - swap the transport only
        base_url="http://backend/api/v1",
        transport=httpx.MockTransport(record),
        headers={AGENT_HEADER: IDENTITY},
    )
    return client, seen


def _json(status: int, body: object) -> httpx.Response:
    return httpx.Response(status, json=body)


async def test_every_verb_sends_the_agent_header() -> None:
    client, seen = _client(lambda r: _json(200, {"ok": True}))
    await client.get("/jobs")
    await client.post("/datasets", json_body={"name": "x"})
    await client.put("/settings/hf_token", json_body={"value": "v"})
    await client.patch("/datasets/ds_1", json_body={})
    await client.delete("/versions/ver_1", json_body={"reason": "r"})
    await client.post_multipart("/sources/uploads", files=[("files", ("a", b"x", "t"))], data={})
    await client.get("/api/health")
    assert len(seen) == 7
    assert all(r.headers[AGENT_HEADER] == IDENTITY for r in seen)
    assert seen[-1].url.path == "/api/health"
    assert seen[0].url.path == "/api/v1/jobs"


async def test_the_header_is_re_asserted_per_call_even_if_the_defaults_lose_it() -> None:
    client, seen = _client(lambda r: _json(200, {}))
    client._http.headers.pop(AGENT_HEADER)  # noqa: SLF001
    await client.get("/jobs")
    assert seen[0].headers[AGENT_HEADER] == IDENTITY


def test_request_takes_no_headers_argument() -> None:
    assert "headers" not in inspect.signature(DataworksClient.request).parameters


async def test_a_2xx_html_page_is_an_error_not_an_empty_result() -> None:
    client, _ = _client(
        lambda r: httpx.Response(200, text="<html>", headers={"content-type": "text/html"})
    )
    with pytest.raises(DataworksError) as exc:
        await client.get("/datasets")
    assert exc.value.code == "NON_JSON_RESPONSE"


async def test_a_2xx_json_text_from_a_non_json_page_is_still_an_error() -> None:
    """A proxy page whose body happens to parse as JSON (``{}``) is not the API: decided by the
    content type, never by whether the body parses (control K2 survived without this)."""
    client, _ = _client(
        lambda r: httpx.Response(200, text="{}", headers={"content-type": "text/html"})
    )
    with pytest.raises(DataworksError) as exc:
        await client.get("/datasets")
    assert exc.value.code == "NON_JSON_RESPONSE"


async def test_an_error_envelope_surfaces_code_message_and_details() -> None:
    body = {
        "error": {"code": "NOT_FOUND", "message": "No dataset ds_9.", "details": {"id": "ds_9"}}
    }
    client, _ = _client(lambda r: _json(404, body))
    with pytest.raises(DataworksError) as exc:
        await client.get("/datasets/ds_9")
    assert (exc.value.status, exc.value.code, exc.value.message) == (
        404,
        "NOT_FOUND",
        "No dataset ds_9.",
    )
    assert exc.value.details == {"id": "ds_9"}


async def test_a_non_json_error_is_named() -> None:
    client, _ = _client(lambda r: httpx.Response(502, text="Bad gateway"))
    with pytest.raises(DataworksError) as exc:
        await client.get("/jobs")
    assert exc.value.code == "NON_JSON_ERROR"


async def test_an_empty_2xx_is_an_empty_dict() -> None:
    client, _ = _client(lambda r: httpx.Response(204))
    assert await client.delete("/settings/x") == {}


async def test_a_202_pending_body_is_returned_unchanged() -> None:
    pending = {"approval_id": "apr_1", "status": "pending", "action": "hub_push"}
    client, _ = _client(lambda r: _json(202, pending))
    assert await client.post("/publishes", json_body={}) == pending


async def test_unreachable_and_timeout_are_named() -> None:
    def refuse(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client, _ = _client(refuse)
    with pytest.raises(DataworksError) as exc:
        await client.get("/jobs")
    assert exc.value.code == "BACKEND_UNREACHABLE"

    def slow(_r: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    client, _ = _client(slow)
    with pytest.raises(DataworksError) as exc:
        await client.post("/publishes", json_body={})
    assert "may already have been applied" in exc.value.message


def _string_constant(path: Path, name: str) -> str:
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            if isinstance(node.value, ast.Constant):
                return str(node.value.value)
            if isinstance(node.value, ast.Call) and node.value.args:
                arg = node.value.args[0]
                if isinstance(arg, ast.Constant):
                    return str(arg.value)
    raise AssertionError(f"{name} not found in {path}")


def test_the_header_name_and_pattern_equal_the_backends_by_ast() -> None:
    """The MCP package must not import the backend, so the two constants are read as source."""
    backend = SRC / "core/agent_origin.py"
    assert _string_constant(backend, "AGENT_HEADER") == AGENT_HEADER
    assert _string_constant(backend, "_AGENT_VALUE") == AGENT_IDENTITY_PATTERN


@pytest.mark.parametrize("name", ["get", "post", "put", "patch", "delete", "post_multipart"])
def test_the_recording_client_signatures_equal_the_real_client(name: str) -> None:
    real = inspect.signature(getattr(DataworksClient, name))
    fake = inspect.signature(getattr(RecordingClient, name))
    assert str(real) == str(fake), f"{name}: real {real} vs recorder {fake}"
