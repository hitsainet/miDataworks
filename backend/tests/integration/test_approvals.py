"""Approvals and agent origin (ADR-013, C6; Foundation tasks 9.1–9.5).

Mutation controls: make the gate run agent calls ungated; make a raising predicate fail open;
drop the ``when`` short-circuit; drop the status check that makes approval run once. Each must
turn this file red (recorded in the controls file).
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.v1.endpoints.approvals import router as approvals_router
from src.core.agent_origin import (
    APPROVAL_ACTIONS,
    EXECUTORS,
    Actor,
    get_actor,
    requires_approval_when_agent,
)
from src.core.database import get_db, get_sync_engine
from src.core.errors import install_error_handlers

AGENT = {"X-Dataworks-Agent": "agent:dataworks-mcp"}
EXECUTED: list[dict[str, Any]] = []


class Work(BaseModel):
    rows: int
    note: str = ""


def _big(values: dict[str, Any], _db: AsyncSession) -> bool:
    return values["body"].rows > 10


def _raises(values: dict[str, Any], _db: AsyncSession) -> bool:
    raise RuntimeError("cannot count")


router = APIRouter(prefix="/test")


@router.post("/gated/{target}")
@requires_approval_when_agent("hub_push")
async def gated(
    target: str, body: Work, db: AsyncSession = Depends(get_db), actor: Actor = Depends(get_actor)
) -> dict[str, Any]:
    EXECUTED.append(
        {"target": target, "rows": body.rows, "origin": actor.origin, "approval": actor.approval_id}
    )
    return {"id": f"job_{target}", "rows": body.rows}


@router.post("/conditional")
@requires_approval_when_agent("agent_label_rows", when=_big)
async def conditional(
    body: Work, db: AsyncSession = Depends(get_db), actor: Actor = Depends(get_actor)
) -> dict[str, Any]:
    EXECUTED.append({"rows": body.rows, "origin": actor.origin})
    return {"id": "job_conditional"}


@router.post("/raising")
@requires_approval_when_agent("agent_label_rows", when=_raises)
async def raising(
    body: Work, db: AsyncSession = Depends(get_db), actor: Actor = Depends(get_actor)
) -> dict[str, Any]:
    EXECUTED.append({"rows": body.rows})
    return {"id": "job_raising"}


@router.post("/ungated")
async def ungated(body: Work, actor: Actor = Depends(get_actor)) -> dict[str, Any]:
    """A control route WITHOUT the decorator: agents go straight through."""
    EXECUTED.append({"rows": body.rows, "origin": actor.origin})
    return {"id": "job_ungated"}


def _app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router)
    app.include_router(approvals_router)
    return app


@pytest.fixture
async def http(clean_db: None, operator_name: str) -> httpx.AsyncClient:
    EXECUTED.clear()
    transport = httpx.ASGITransport(app=_app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        yield client


async def test_operator_calls_are_never_gated(http: httpx.AsyncClient) -> None:
    response = await http.post("/test/gated/a", json={"rows": 5})
    assert response.status_code == 200
    assert EXECUTED == [{"target": "a", "rows": 5, "origin": "operator", "approval": None}]


async def test_an_agent_call_is_stored_and_answered_202(http: httpx.AsyncClient) -> None:
    response = await http.post("/test/gated/b", json={"rows": 7}, headers=AGENT)
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "pending" and body["action"] == "hub_push"
    assert body["approval_id"].startswith("apr_") and body["request_digest"].startswith("sha256:")
    assert EXECUTED == [], "nothing runs before the operator approves"
    stored = (await http.get(f"/api/v1/approvals/{body['approval_id']}")).json()
    assert stored["requested_by"] == "agent:dataworks-mcp"
    assert stored["payload"] == {"target": "b", "body": {"rows": 7, "note": ""}}


async def test_the_when_predicate_decides_for_agents(http: httpx.AsyncClient) -> None:
    small = await http.post("/test/conditional", json={"rows": 10}, headers=AGENT)
    big = await http.post("/test/conditional", json={"rows": 11}, headers=AGENT)
    assert small.status_code == 200 and big.status_code == 202
    assert EXECUTED == [{"rows": 10, "origin": "agent"}]


async def test_a_raising_predicate_fails_closed(http: httpx.AsyncClient) -> None:
    response = await http.post("/test/raising", json={"rows": 1}, headers=AGENT)
    assert response.status_code == 202
    assert EXECUTED == []


async def test_an_undecorated_route_lets_an_agent_through(http: httpx.AsyncClient) -> None:
    """The negative half of 9.5: without the decorator nothing stops an agent."""
    response = await http.post("/test/ungated", json={"rows": 99}, headers=AGENT)
    assert response.status_code == 200 and EXECUTED == [{"rows": 99, "origin": "agent"}]


async def test_approving_runs_the_stored_action_exactly_once(http: httpx.AsyncClient) -> None:
    approval_id = (await http.post("/test/gated/c", json={"rows": 3}, headers=AGENT)).json()[
        "approval_id"
    ]
    first = await http.post(f"/api/v1/approvals/{approval_id}/approve")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["status"] == "executed" and body["decided_by"] == "Test Operator"
    assert body["result_id"] == "job_c" and body["result_kind"] == "job"
    assert EXECUTED == [{"target": "c", "rows": 3, "origin": "agent", "approval": approval_id}]
    second = await http.post(f"/api/v1/approvals/{approval_id}/approve")
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "APPROVAL_NOT_PENDING"
    assert second.json()["error"]["details"]["status"] == "executed"
    assert len(EXECUTED) == 1


async def test_concurrent_approvals_run_once(http: httpx.AsyncClient) -> None:
    approval_id = (await http.post("/test/gated/d", json={"rows": 4}, headers=AGENT)).json()[
        "approval_id"
    ]
    results = await asyncio.gather(
        *[http.post(f"/api/v1/approvals/{approval_id}/approve") for _ in range(4)]
    )
    assert sorted(r.status_code for r in results) == [200, 409, 409, 409]
    assert len(EXECUTED) == 1


async def test_reject_and_then_approve_is_refused(http: httpx.AsyncClient) -> None:
    approval_id = (await http.post("/test/gated/e", json={"rows": 1}, headers=AGENT)).json()[
        "approval_id"
    ]
    rejected = await http.post(
        f"/api/v1/approvals/{approval_id}/reject", json={"reason": "not now"}
    )
    assert rejected.json()["status"] == "rejected" and rejected.json()["reason"] == "not now"
    approve = await http.post(f"/api/v1/approvals/{approval_id}/approve")
    assert approve.status_code == 409 and EXECUTED == []


async def test_an_agent_cannot_decide(http: httpx.AsyncClient) -> None:
    approval_id = (await http.post("/test/gated/f", json={"rows": 1}, headers=AGENT)).json()[
        "approval_id"
    ]
    for verb, body in (("approve", None), ("reject", {"reason": "x"})):
        response = await http.post(
            f"/api/v1/approvals/{approval_id}/{verb}", json=body, headers=AGENT
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "AGENT_CANNOT_DECIDE"
    assert EXECUTED == []


async def test_an_expired_approval_cannot_run(http: httpx.AsyncClient) -> None:
    approval_id = (await http.post("/test/gated/g", json={"rows": 1}, headers=AGENT)).json()[
        "approval_id"
    ]
    with get_sync_engine().begin() as conn:
        conn.execute(
            text("UPDATE dw_approvals SET expires_at = now() - interval '1 minute' WHERE id = :i"),
            {"i": approval_id},
        )
    response = await http.post(f"/api/v1/approvals/{approval_id}/approve")
    assert (
        response.status_code == 409 and response.json()["error"]["details"]["status"] == "expired"
    )
    assert EXECUTED == []


async def test_the_janitor_expires_pending_approvals(http: httpx.AsyncClient) -> None:
    from src.core.database import get_sync_db
    from src.services.approval_service import expire_due

    approval_id = (await http.post("/test/gated/h", json={"rows": 1}, headers=AGENT)).json()[
        "approval_id"
    ]
    with get_sync_engine().begin() as conn:
        conn.execute(
            text("UPDATE dw_approvals SET expires_at = now() - interval '1 s' WHERE id = :i"),
            {"i": approval_id},
        )
    with get_sync_db() as db:
        assert expire_due(db) == 1
    assert (await http.get(f"/api/v1/approvals/{approval_id}")).json()["status"] == "expired"


async def test_a_tampered_payload_fails_instead_of_running(http: httpx.AsyncClient) -> None:
    approval_id = (await http.post("/test/gated/i", json={"rows": 1}, headers=AGENT)).json()[
        "approval_id"
    ]
    with get_sync_engine().begin() as conn:
        conn.execute(
            text(
                """UPDATE dw_approvals SET payload = jsonb_set(payload, '{body,rows}', '9999') WHERE id = :i"""
            ),
            {"i": approval_id},
        )
    response = await http.post(f"/api/v1/approvals/{approval_id}/approve")
    assert response.status_code == 409 and response.json()["error"]["code"] == "APPROVAL_TAMPERED"
    assert EXECUTED == []


async def test_list_filters_by_status(http: httpx.AsyncClient) -> None:
    await http.post("/test/gated/j", json={"rows": 1}, headers=AGENT)
    pending = (await http.get("/api/v1/approvals", params={"status": "pending"})).json()[
        "approvals"
    ]
    executed = (await http.get("/api/v1/approvals", params={"status": "executed"})).json()[
        "approvals"
    ]
    assert len(pending) == 1 and executed == []


def test_the_seven_action_names_are_registered() -> None:
    """Task 9.4 (with P-11, S3-01, S3-08): 010 FTDD section 5.3 owns the list."""
    assert set(APPROVAL_ACTIONS) == {
        "hub_push",
        "agent_label_rows",
        "version_delete",
        "millm_model_load",
        "secret_write",
        "source_annotate",
        "gate_target_write",
    }


def test_an_unknown_action_name_is_refused_at_import_time() -> None:
    with pytest.raises(ValueError):
        requires_approval_when_agent("delete_everything")


def test_a_route_without_the_two_dependencies_is_refused() -> None:
    with pytest.raises(TypeError):

        @requires_approval_when_agent("hub_push")
        async def bad(body: Work) -> None:  # noqa: ARG001
            return None


async def test_the_actions_route_lists_them(client: httpx.AsyncClient) -> None:
    actions = (await client.get("/api/v1/approvals/actions")).json()["actions"]
    assert set(actions) == set(APPROVAL_ACTIONS)


def test_the_live_app_gates_its_secret_writes() -> None:
    """Foundation attaches secret_write to the settings and endpoint-key writes (010 §5.3)."""
    keys = {key for key, ex in EXECUTORS.items() if ex.action == "secret_write"}
    assert "src.api.v1.endpoints.settings.put_setting" in keys
    assert "src.api.v1.endpoints.settings.delete_setting" in keys
    assert "src.api.v1.endpoints.endpoint_roles.put_role" in keys


async def test_an_approved_secret_write_stores_the_real_secret(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    from src.core.encryption import decrypt_value

    token = "hf_ApprovedTokenValue_000000000000"
    pending = await client.put("/api/v1/settings/hf_token", json={"value": token}, headers=AGENT)
    approval_id = pending.json()["approval_id"]
    approved = await client.post(f"/api/v1/approvals/{approval_id}/approve")
    assert approved.status_code == 200 and approved.json()["status"] == "executed", approved.text
    with get_sync_engine().connect() as conn:
        stored = conn.execute(
            text("SELECT value FROM dw_app_settings WHERE key='hf_token'")
        ).scalar()
        secret_payload = conn.execute(text("SELECT secret_payload FROM dw_approvals")).scalar()
    assert decrypt_value(stored) == token
    assert secret_payload is None, "the encrypted secret is cleared once the approval is decided"
    assert token not in approved.text
