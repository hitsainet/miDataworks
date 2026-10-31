"""005 mutation round (2026-10-17), survivor A10: an agent's secret writes OTHER than the HF import
token — an endpoint role's API key and the ``hf_token`` setting — wait for ``secret_write`` and the
secret is held only encrypted. Dropping ``secret_fields`` from the endpoint-role route left the
whole suite green while the key would have been stored in the approval's ``payload`` in clear.
Approving applies the real key (decrypted), so the guard also proves the round trip."""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import select

from src.core.database import sync_session_factory
from src.models.approval import Approval

AGENT = {"X-Dataworks-Agent": "agent:mcp"}
SECRET = "sk-agent-supplied-key-6f1e9a"

CASES = [
    (
        "/api/v1/endpoint-roles/judge",
        {
            "protocol": "openai_chat",
            "base_url": "http://judge.test/v1",
            "model_id": "j",
            "api_key": SECRET,
        },
        "api_key",
    ),
    ("/api/v1/settings/hf_token", {"value": SECRET}, "value"),
]


@pytest.mark.parametrize(("path", "payload", "field"), CASES, ids=["endpoint_role", "hf_token"])
async def test_an_agent_secret_write_waits_and_is_held_only_encrypted(
    client: httpx.AsyncClient,
    operator_name: str,
    caplog: pytest.LogCaptureFixture,
    path: str,
    payload: dict,
    field: str,
) -> None:
    response = await client.put(path, json=payload, headers=AGENT)
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["action"] == "secret_write" and body["status"] == "pending"
    with sync_session_factory()() as db:
        row = db.execute(select(Approval).where(Approval.id == body["approval_id"])).scalar_one()
        assert SECRET not in json.dumps(row.payload) and SECRET not in row.summary
        assert row.payload["body"][field].startswith("hmac-sha256:")
        assert row.secret_payload and SECRET not in row.secret_payload
    assert SECRET not in caplog.text and SECRET not in response.text
    approved = await client.post(f"/api/v1/approvals/{body['approval_id']}/approve")
    assert approved.status_code == 200 and approved.json()["status"] == "executed", approved.text
    with sync_session_factory()() as db:
        row = db.execute(select(Approval).where(Approval.id == body["approval_id"])).scalar_one()
        assert row.secret_payload is None


async def test_the_approved_endpoint_key_is_the_real_key(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    path, payload, _ = CASES[0]
    pending = (await client.put(path, json=payload, headers=AGENT)).json()
    await client.post(f"/api/v1/approvals/{pending['approval_id']}/approve")
    from src.services.endpoint_resolver import resolve

    with sync_session_factory()() as db:
        assert resolve("judge", db).api_key == SECRET
