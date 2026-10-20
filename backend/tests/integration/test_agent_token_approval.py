"""An agent-supplied token waits for ``secret_write`` (001 FTASKS 10.6, 10.7; FR-001.35, P-11).

An agent request carrying a token answers 202 with an approval; nothing is enqueued and nothing is
put in the ephemeral store; the token is in neither ``payload`` (the digest input) nor the summary
nor the log, only encrypted in ``secret_payload``. Approve runs the import once with tier
``per_import``; reject and expiry delete the encrypted token. An agent without a token, and an
operator with one, are not gated.
"""

from __future__ import annotations

import json
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select, update

from src.core import ephemeral_secrets
from src.core.clock import utc_now
from src.core.database import sync_session_factory
from src.models.approval import Approval
from src.services.approval_service import expire_due
from tests.support.hf_mock import COLBERT
from tests.support.source_fixtures import HfEnv, hf_env, source_row

__all__ = ["hf_env"]
AGENT = {"X-Dataworks-Agent": "agent:mcp"}
TOKEN = "hf_agent_supplied_token_9"


def approval(approval_id: str) -> Approval:
    with sync_session_factory()() as db:
        row = db.execute(select(Approval).where(Approval.id == approval_id)).scalar_one()
        db.expunge(row)
        return row


def eph_keys() -> list[bytes]:
    return list(ephemeral_secrets.redis_client().scan_iter(match=ephemeral_secrets.PREFIX + "*"))


@pytest.fixture
def empty_ephemeral_store() -> None:
    for key in eph_keys():
        ephemeral_secrets.redis_client().delete(key)


@pytest.mark.parametrize("path", ["/api/v1/sources/hf", "/api/v1/sources/hf/preview"])
async def test_an_agent_token_waits_and_is_held_only_encrypted(
    client: httpx.AsyncClient,
    hf_env: HfEnv,
    operator_name: str,
    empty_ephemeral_store: None,
    caplog: pytest.LogCaptureFixture,
    path: str,
) -> None:
    response = await client.post(
        path, json={"repo_id": COLBERT, "access_token": TOKEN}, headers=AGENT
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["action"] == "secret_write" and body["status"] == "pending"
    assert hf_env.sent == [] and eph_keys() == []
    row = approval(body["approval_id"])
    assert TOKEN not in json.dumps(row.payload) and TOKEN not in row.summary
    assert row.payload["body"]["access_token"].startswith("hmac-sha256:")
    assert row.secret_payload and TOKEN not in row.secret_payload
    assert TOKEN not in caplog.text and TOKEN not in response.text


async def test_approve_runs_the_import_once_with_the_per_import_tier(
    client: httpx.AsyncClient, hf_env: HfEnv, operator_name: str, empty_ephemeral_store: None
) -> None:
    pending = await client.post(
        "/api/v1/sources/hf", json={"repo_id": COLBERT, "access_token": TOKEN}, headers=AGENT
    )
    approval_id = pending.json()["approval_id"]
    approved = await client.post(f"/api/v1/approvals/{approval_id}/approve")
    assert approved.status_code == 200 and approved.json()["status"] == "executed", approved.text
    assert [n for n, _ in hf_env.sent] == ["midataworks.sources.import_source"]
    [result] = hf_env.run_imports()
    assert hf_env.loader.calls[0]["token"] == TOKEN
    source = source_row(result["source_id"])
    assert source.token_tier == "per_import" and source.created_by == "agent:mcp"
    assert source.created_by_origin == "agent"
    assert approval(approval_id).secret_payload is None
    assert eph_keys() == [], "the worker's GETDEL removed the token"
    again = await client.post(f"/api/v1/approvals/{approval_id}/approve")
    assert again.status_code == 409
    assert len(hf_env.loader.calls) == 1


async def test_reject_deletes_the_encrypted_token(
    client: httpx.AsyncClient, hf_env: HfEnv, operator_name: str
) -> None:
    pending = await client.post(
        "/api/v1/sources/hf", json={"repo_id": COLBERT, "access_token": TOKEN}, headers=AGENT
    )
    approval_id = pending.json()["approval_id"]
    rejected = await client.post(
        f"/api/v1/approvals/{approval_id}/reject", json={"reason": "not this repository"}
    )
    assert rejected.status_code == 200
    assert approval(approval_id).secret_payload is None and hf_env.sent == []


async def test_expiry_deletes_the_encrypted_token(
    client: httpx.AsyncClient, hf_env: HfEnv, operator_name: str
) -> None:
    pending = await client.post(
        "/api/v1/sources/hf", json={"repo_id": COLBERT, "access_token": TOKEN}, headers=AGENT
    )
    approval_id = pending.json()["approval_id"]
    row = approval(approval_id)
    assert timedelta(hours=23) < row.expires_at - utc_now() <= timedelta(hours=24)
    with sync_session_factory()() as db:
        db.execute(
            update(Approval)
            .where(Approval.id == approval_id)
            .values(expires_at=utc_now() - timedelta(seconds=1))
        )
        db.commit()
        assert expire_due(db) == 1
    expired = approval(approval_id)
    assert expired.status == "expired" and expired.secret_payload is None


async def test_an_agent_without_a_token_is_not_gated(
    client: httpx.AsyncClient, hf_env: HfEnv, operator_name: str
) -> None:
    response = await client.post("/api/v1/sources/hf", json={"repo_id": COLBERT}, headers=AGENT)
    assert response.status_code == 202 and "job_id" in response.json()
    assert hf_env.sent and hf_env.sent[0][0] == "midataworks.sources.import_source"


async def test_an_operator_with_a_token_is_not_gated(
    client: httpx.AsyncClient, hf_env: HfEnv, operator_name: str
) -> None:
    response = await client.post(
        "/api/v1/sources/hf", json={"repo_id": COLBERT, "access_token": TOKEN}
    )
    assert response.status_code == 202 and "job_id" in response.json()
    with sync_session_factory()() as db:
        assert db.execute(select(Approval)).first() is None


def test_both_token_routes_declare_secret_write_in_openapi() -> None:
    from src.main import fastapi_app

    paths = fastapi_app.openapi()["paths"]
    for path in ("/api/v1/sources/hf", "/api/v1/sources/hf/preview"):
        assert paths[path]["post"].get("x-approval-action") == "secret_write", path
    assert paths["/api/v1/sources/{source_id}/annotations"]["post"]["x-approval-action"] == (
        "source_annotate"
    )
