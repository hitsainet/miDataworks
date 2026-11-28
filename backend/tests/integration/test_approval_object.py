"""The suite's ``approval`` object on every pending answer and every approval read (022 FR-022.3 - .6, .31).

Shapes come from the vendored ``mistudio.result-language/v1`` (``tests/support/result_language.py``),
never retyped here.

- **Every action a route uses** (ten of the eleven; ``millm_model_load`` has no route): the 202 body
  is built in ONE place, the gate's wrapper, so a stub route per action proves the builder for each;
  two real routes prove the wiring through the app.
- **Every approval read** (get, list, archive, unarchive, approve, reject) carries it.
- **Status**: each stored status maps to itself; a pending row past its expiry reads ``expired``
  while the legacy ``status`` keeps ``pending`` (022-D9).
- **Server-executed** (FR-022.31): ``then`` is ``server_runs``, no ``call_again_with``; an agent that
  re-submits with an invented approval header files a SECOND pending row, the first unchanged.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.v1.endpoints.approvals import router as approvals_router
from src.core.agent_origin import APPROVAL_ACTIONS, Actor, get_actor, requires_approval_when_agent
from src.core.clock import utc_now
from src.core.database import get_db, get_sync_engine, sync_session_factory
from src.core.errors import install_error_handlers
from src.models.approval import Approval
from tests.support.result_language import status_enum, validate

AGENT = {"X-Dataworks-Agent": "agent:dataworks-mcp"}
LEGACY_KEYS = {"approval_id", "status", "action", "request_digest", "expires_at", "hint"}
#: Actions no route uses, with the reason (tests/integration/test_approval_coverage.py UNATTACHED).
NO_ROUTE = {"millm_model_load"}


class Work(BaseModel):
    rows: int


router = APIRouter(prefix="/test")


def _route(action: str) -> None:
    async def handler(
        body: Work, db: AsyncSession = Depends(get_db), actor: Actor = Depends(get_actor)
    ) -> dict[str, Any]:
        return {"ran": action, "rows": body.rows}

    # One executor key per action (the gate keys executors by module and qualname).
    handler.__qualname__ = handler.__name__ = f"approval_object_{action}"
    gated = requires_approval_when_agent(action, summary=lambda _: f"do {action} for the test")
    router.post(f"/{action}")(gated(handler))


ROUTED = sorted(set(APPROVAL_ACTIONS) - NO_ROUTE)
for _action in ROUTED:
    _route(_action)


def _app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router)
    app.include_router(approvals_router)
    return app


@pytest.fixture
async def http(clean_db: None, operator_name: str) -> Any:
    transport = httpx.ASGITransport(app=_app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        yield client


def check_approval(body: dict[str, Any]) -> dict[str, Any]:
    approval: dict[str, Any] = body["approval"]
    validate("approval", approval)
    assert approval["decide_in"] == "midataworks"
    assert approval["then"] == "server_runs" and "call_again_with" not in approval
    return approval


def test_every_routed_action_is_covered() -> None:
    """Named, not counted: 021's ``sensitivity_declare`` (B3 port, 2026-11-24) made the count 11, and a
    count alone would stay green over one action swapped for another."""
    assert ROUTED == [
        "agent_label_rows",
        "code_rule_save",
        "config_write",
        "gate_target_write",
        "hub_push",
        "paid_classifier_run",
        "secret_write",
        "sensitivity_declare",
        "source_annotate",
        "version_delete",
        "warning_level_write",
    ], ROUTED


@pytest.mark.parametrize("action", ROUTED)
async def test_every_pending_answer_carries_the_approval(
    http: httpx.AsyncClient, action: str
) -> None:
    response = await http.post(f"/test/{action}", json={"rows": 1}, headers=AGENT)
    assert response.status_code == 202, response.text
    body = response.json()
    assert LEGACY_KEYS <= set(body), "a legacy key was dropped"
    approval = check_approval(body)
    assert approval["id"] == body["approval_id"]
    assert approval["status"] == "pending" == body["status"]
    assert approval["summary"] == f"do {action} for the test"


async def test_every_approval_read_carries_it(http: httpx.AsyncClient) -> None:
    first = (await http.post("/test/hub_push", json={"rows": 1}, headers=AGENT)).json()
    second = (await http.post("/test/hub_push", json={"rows": 2}, headers=AGENT)).json()
    a, b = first["approval_id"], second["approval_id"]
    read = (await http.get(f"/api/v1/approvals/{a}")).json()
    assert check_approval(read)["status"] == "pending"
    listed = (await http.get("/api/v1/approvals")).json()["approvals"]
    assert listed and all(check_approval(row) for row in listed)
    approved = (await http.post(f"/api/v1/approvals/{a}/approve")).json()
    assert check_approval(approved)["status"] == "executed" == approved["status"]
    rejected = (await http.post(f"/api/v1/approvals/{b}/reject", json={"reason": "no"})).json()
    assert check_approval(rejected)["status"] == "rejected"
    # Only a rejected or expired request may be archived.
    archived = (await http.post(f"/api/v1/approvals/{b}/archive", json={})).json()
    assert check_approval(archived)["status"] == "rejected"
    unarchived = (await http.post(f"/api/v1/approvals/{b}/unarchive", json={})).json()
    assert check_approval(unarchived)["status"] == "rejected"


async def test_an_unswept_expired_request_reads_expired(http: httpx.AsyncClient) -> None:
    pending = (await http.post("/test/hub_push", json={"rows": 1}, headers=AGENT)).json()
    with sync_session_factory()() as db:
        db.execute(
            update(Approval)
            .where(Approval.id == pending["approval_id"])
            .values(expires_at=utc_now() - timedelta(minutes=1))
        )
        db.commit()
    read = (await http.get(f"/api/v1/approvals/{pending['approval_id']}")).json()
    assert read["status"] == "pending", "the legacy status keeps the row's value"
    assert check_approval(read)["status"] == "expired"


def test_every_status_the_service_writes_maps_to_itself_and_is_in_the_contract() -> None:
    from src.models.approval import APPROVAL_STATUSES
    from src.services.result_language import STATUS_MAP

    # The registry (the table's check constraint is built from it), never a hand list: a status
    # added there and not mapped would make every approval read raise (R3 review fix (OR3-022-5),
    # 2026-11-16).
    written = set(APPROVAL_STATUSES)
    assert set(STATUS_MAP) == written, sorted(set(STATUS_MAP) ^ written)
    for status in written:
        assert STATUS_MAP[status] == status
        assert status in status_enum()
    assert "approved" not in STATUS_MAP.values(), "miDataworks has no approved state"


def test_a_narrowed_contract_would_refuse_a_real_status() -> None:
    """FR-022.2's AC: a test copy with ``approval.status`` narrowed to pending refuses executed."""
    import copy

    import jsonschema

    from tests.support.result_language import schema

    narrowed = copy.deepcopy(schema())
    narrowed["$defs"]["approval_status"]["enum"] = ["pending"]
    row = {
        "id": "apr_1",
        "status": "executed",
        "decide_in": "midataworks",
        "then": "server_runs",
        "summary": "x",
    }
    validate("approval", row)
    with pytest.raises(jsonschema.ValidationError):
        validate("approval", row, narrowed)


# -- through the real app ----------------------------------------------------------------------


async def test_a_real_gated_route_answers_the_approval(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    response = await client.put(
        "/api/v1/settings/hf_token", json={"value": "hf_agent_token_022"}, headers=AGENT
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert LEGACY_KEYS <= set(body)
    assert check_approval(body)["id"] == body["approval_id"]
    read = (await client.get(f"/api/v1/approvals/{body['approval_id']}")).json()
    assert check_approval(read)["status"] == "pending"


async def test_server_executed_resubmitting_files_a_second_request(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    """FR-022.31: there is no approval-id re-call header; a re-submission is a new request."""
    body = {"value": "hf_agent_token_022"}
    first = (await client.put("/api/v1/settings/hf_token", json=body, headers=AGENT)).json()
    again = await client.put(
        "/api/v1/settings/hf_token",
        json=body,
        headers={**AGENT, "X-Dataworks-Approval-Request": first["approval_id"]},
    )
    assert again.status_code == 202, again.text
    assert again.json()["approval_id"] != first["approval_id"]
    assert check_approval(again.json())["status"] == "pending"
    with get_sync_engine().connect() as conn:
        statuses = dict(conn.execute(text("SELECT id, status FROM dw_approvals")).all())
    assert statuses == {first["approval_id"]: "pending", again.json()["approval_id"]: "pending"}
