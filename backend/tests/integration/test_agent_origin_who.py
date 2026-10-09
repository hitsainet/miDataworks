"""Agent origin at REST: the header refused when malformed on EVERY route, activity recorded with
the route template, "who" from the header and never from a body (010 FTASKS 3.1-3.5)."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from src.core.database import sync_session_factory
from src.main import fastapi_app
from src.models.agent_request import AgentRequest

AGENT = {"X-Dataworks-Agent": "agent:dataworks-mcp"}


def _activity() -> list[tuple[str, str, str, int]]:
    with sync_session_factory()() as db:
        rows = db.execute(select(AgentRequest).order_by(AgentRequest.created_at)).scalars()
        return [(r.identity, r.method, r.route, r.status_code) for r in rows]


@pytest.mark.parametrize("value", ["", "agent:", "Agent:x", "agent:UPPER", "operator", "agent:a b"])
async def test_a_malformed_header_is_refused_on_a_route_that_never_reads_it(
    client: httpx.AsyncClient, value: str
) -> None:
    """``GET /api/v1/jobs`` does not declare the actor; the middleware still refuses."""
    response = await client.get("/api/v1/jobs", headers={"X-Dataworks-Agent": value})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_AGENT_IDENTITY"
    assert _activity() == []


async def test_an_agent_request_records_its_route_template_not_the_path(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/v1/jobs/job_does_not_exist", headers=AGENT)
    assert response.status_code == 404
    assert _activity() == [("agent:dataworks-mcp", "GET", "/api/v1/jobs/{job_id}", 404)]


async def test_an_operator_request_records_nothing(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/v1/jobs")).status_code == 200
    assert _activity() == []


async def test_an_activity_write_failure_never_fails_the_request(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from src.services import agent_activity_service

    def broken() -> object:
        raise RuntimeError("database gone")

    monkeypatch.setattr(agent_activity_service, "async_session_factory", broken)
    response = await client.get("/api/v1/jobs", headers=AGENT)
    assert response.status_code == 200
    assert "Could not record agent request" in caplog.text


async def test_the_operator_cannot_be_impersonated_through_a_body_who(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    """A body ``started_by`` is refused (requests forbid extra keys) or ignored; the job records
    the header identity with origin ``agent``."""
    created = await client.post(
        "/api/v1/datasets", json={"name": "ds-who", "started_by": "Sean"}, headers=AGENT
    )
    assert created.status_code == 422
    response = await client.post(
        "/api/v1/jobs/selftest", json={"duration_seconds": 0.1, "rows": 1}, headers=AGENT
    )
    assert response.status_code == 201, response.text
    job = response.json()
    assert (job["started_by"], job["started_by_origin"]) == ("agent:dataworks-mcp", "agent")


def test_no_request_body_schema_accepts_a_who_field() -> None:
    """FR-010.28: "who" comes from the header (agent) or Settings (operator), never a body."""
    spec = fastapi_app.openapi()
    schemas = spec["components"]["schemas"]
    request_schemas: set[str] = set()
    for ops in spec["paths"].values():
        for op in ops.values():
            for media in op.get("requestBody", {}).get("content", {}).values():
                ref = media["schema"].get("$ref") or next(
                    (a.get("$ref") for a in media["schema"].get("anyOf", []) if a.get("$ref")), None
                )
                if ref:
                    request_schemas.add(ref.rsplit("/", 1)[-1])
    assert request_schemas, "found no request bodies: the scan asserts nothing"
    for name in request_schemas:
        props = set(schemas[name].get("properties", {}))
        assert not props & {"who", "started_by", "decided_by"}, (name, props)


async def test_the_activity_is_pruned_after_the_retention(client: httpx.AsyncClient) -> None:
    from sqlalchemy import text

    from src.core.database import get_sync_db, get_sync_engine
    from src.services.agent_activity_service import prune_agent_requests

    await client.get("/api/v1/jobs", headers=AGENT)
    await client.get("/api/v1/approvals", headers=AGENT)
    with get_sync_engine().begin() as conn:
        conn.execute(
            text(
                "UPDATE dw_agent_requests SET created_at = now() - interval '31 days' "
                "WHERE route = '/api/v1/jobs'"
            )
        )
    with get_sync_db() as db:
        assert prune_agent_requests(db) == 1
    assert [r[2] for r in _activity()] == ["/api/v1/approvals"]
