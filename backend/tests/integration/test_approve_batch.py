"""Approve all waiting settings changes in one decision (ENH-006 D79, operator 2026-10-29).

``POST /api/v1/approvals/approve-batch`` with ``{"approval_ids": [...]}``: operator only, the
Settings PIN asked once for the whole decision, every id checked BEFORE anything is approved
(409 naming each refused id), each request approved through the single approve path (it runs
once, is recorded on its own row as decided by the operator, with the shared ``batch_id``), one
failure does not stop the others. A "settings change" is a ``config_write`` or ``secret_write``
request through a PIN-gated Settings route, read from the live route table; a source import that
carries a token is ``secret_write`` too and is refused here.

Negative controls (``0xcc/reviews/approve_all_settings_controls_2026-10-29.md``): the route's
registration removed, the pre-check skipped, only the first id approved, the PIN check removed,
the eligibility predicate always true, the batch id not written; each turns this file red.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy import text

from src.core import settings_pin
from src.core.config import env_settings
from src.core.database import get_sync_engine

AGENT = {"X-Dataworks-Agent": "agent:dataworks-mcp"}
HEADER = settings_pin.SESSION_HEADER
PIN = "4711"
ROUTE = "/api/v1/approvals/approve-batch"


@pytest.fixture(autouse=True)
def _fresh_env_settings() -> Any:
    env_settings.cache_clear()
    settings_pin.reset_failures()
    yield
    env_settings.cache_clear()


def _setting(key: str) -> str | None:
    with get_sync_engine().connect() as conn:
        return conn.execute(
            text("SELECT value FROM dw_app_settings WHERE key = :k"), {"k": key}
        ).scalar_one_or_none()


def _rows(ids: list[str]) -> dict[str, dict[str, Any]]:
    with get_sync_engine().connect() as conn:
        found = conn.execute(
            text(
                "SELECT id, status, decided_by, decided_at, batch_id FROM dw_approvals "
                "WHERE id = ANY(:ids)"
            ),
            {"ids": ids},
        ).mappings()
        return {r["id"]: dict(r) for r in found}


async def _queue_settings(client: httpx.AsyncClient) -> list[str]:
    """Three waiting settings changes from an agent: two environment settings and an endpoint."""
    ids = []
    for response in (
        await client.put("/api/v1/settings/log_level", json={"value": "DEBUG"}, headers=AGENT),
        await client.put(
            "/api/v1/settings/endpoint_http_timeout_seconds", json={"value": "45"}, headers=AGENT
        ),
        await client.put(
            "/api/v1/endpoint-roles/judge",
            json={"protocol": "openai_chat", "base_url": "http://judge.test/v1"},
            headers=AGENT,
        ),
    ):
        assert response.status_code == 202, response.text
        assert response.json()["action"] == "config_write"
        ids.append(response.json()["approval_id"])
    return ids


async def _queue_other(action: str, executor_key: str) -> str:
    """A waiting request that is not a settings change."""
    from src.core.database import async_session_factory
    from src.services.approval_service import ApprovalService

    async with async_session_factory()() as db:
        row = await ApprovalService.create_pending(
            db,
            action=action,
            executor_key=executor_key,
            payload={"version_id": "ver_x"},
            secret_fields=(),
            requested_by="agent:dataworks-mcp",
            summary="not a settings change",
        )
        return row.id


async def _set_pin(client: httpx.AsyncClient) -> str:
    response = await client.put("/api/v1/settings/pin", json={"pin": PIN})
    assert response.status_code == 200, response.text
    return str(response.json()["token"])


# --- the happy path ---------------------------------------------------------------------------


async def test_every_listed_settings_change_runs_once_with_one_batch_id(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    ids = await _queue_settings(client)
    assert _setting("log_level") is None

    response = await client.post(ROUTE, json={"approval_ids": ids})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["batch_id"].startswith("apb_") and body["kind"] == "settings"
    assert [r["approval_id"] for r in body["results"]] == ids
    assert [r["status"] for r in body["results"]] == ["executed"] * 3

    # Each ran: the payload reached its route, not only "was called".
    assert _setting("log_level") == "DEBUG"
    assert _setting("endpoint_http_timeout_seconds") == "45.0"
    role = (await client.get("/api/v1/endpoint-roles/judge")).json()
    assert role["base_url"] == "http://judge.test/v1"

    # Each is recorded on its own row, decided by the operator, with the shared batch id.
    rows = _rows(ids)
    assert len(rows) == 3
    for row in rows.values():
        assert row["status"] == "executed"
        assert row["decided_by"] == "Test Operator"
        assert row["decided_at"] is not None
        assert row["batch_id"] == body["batch_id"]

    # History shows each, with the batch id.
    history = (await client.get("/api/v1/approvals?decided=true")).json()["approvals"]
    listed = {a["id"]: a for a in history}
    assert set(ids) <= set(listed)
    assert {listed[i]["batch_id"] for i in ids} == {body["batch_id"]}

    # Once: a second batch over the same ids is refused and runs nothing again.
    again = await client.post(ROUTE, json={"approval_ids": ids})
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "APPROVAL_BATCH_REFUSED"


async def test_a_single_approve_records_no_batch_id(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    ids = await _queue_settings(client)
    single = await client.post(f"/api/v1/approvals/{ids[0]}/approve")
    assert single.status_code == 200 and single.json()["batch_id"] is None


# --- the pre-check: nothing is approved unless every id may be ---------------------------------


@pytest.mark.parametrize(
    "kind", ["unknown", "not_settings", "token_import", "already_decided", "expired"]
)
async def test_a_refused_id_refuses_the_whole_batch_before_anything_runs(
    client: httpx.AsyncClient, operator_name: str, kind: str
) -> None:
    ids = await _queue_settings(client)
    if kind == "unknown":
        bad = "apr_000000000000000000000000"
    elif kind == "not_settings":
        bad = await _queue_other("version_delete", "src.api.v1.endpoints.versions.delete_version")
    elif kind == "token_import":
        # secret_write, but through a source route that is not behind the Settings PIN.
        bad = await _queue_other("secret_write", "src.api.v1.endpoints.sources.preview_hf")
    elif kind == "already_decided":
        bad = ids.pop()
        rejected = await client.post(f"/api/v1/approvals/{bad}/reject", json={"reason": "no"})
        assert rejected.status_code == 200
    else:
        bad = ids.pop()
        with get_sync_engine().begin() as conn:
            conn.execute(
                text(
                    "UPDATE dw_approvals SET expires_at = now() - interval '1 minute' WHERE id = :i"
                ),
                {"i": bad},
            )

    # The bad id LAST: an implementation that approves as it checks would have run the others.
    response = await client.post(ROUTE, json={"approval_ids": [*ids, bad]})
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "APPROVAL_BATCH_REFUSED"
    assert [r["approval_id"] for r in error["details"]["refused"]] == [bad]
    assert bad in error["message"]

    assert {r["status"] for r in _rows(ids).values()} == {"pending"}
    assert _setting("log_level") is None
    assert {r["batch_id"] for r in _rows(ids).values()} == {None}


async def test_an_empty_list_is_refused(client: httpx.AsyncClient) -> None:
    response = await client.post(ROUTE, json={"approval_ids": []})
    assert response.status_code == 422


# --- one failure does not stop the others -----------------------------------------------------


async def test_an_item_that_fails_while_running_does_not_stop_the_others(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    ids = await _queue_settings(client)
    # Make the middle request's stored payload invalid for its route: its execution fails.
    with get_sync_engine().begin() as conn:
        payload = conn.execute(
            text("SELECT payload FROM dw_approvals WHERE id = :i"), {"i": ids[1]}
        ).scalar_one()
        payload["body"] = None
        from src.core.canonical_json import canonical_sha256

        conn.execute(
            text(
                "UPDATE dw_approvals SET payload = CAST(:p AS jsonb), request_digest = :d "
                "WHERE id = :i"
            ),
            {"p": __import__("json").dumps(payload), "d": canonical_sha256(payload), "i": ids[1]},
        )

    response = await client.post(ROUTE, json={"approval_ids": ids})
    assert response.status_code == 200, response.text
    statuses = [r["status"] for r in response.json()["results"]]
    assert statuses == ["executed", "failed", "executed"]
    assert response.json()["results"][1]["error"]["code"]
    assert _setting("log_level") == "DEBUG"
    assert (await client.get("/api/v1/endpoint-roles/judge")).json()["base_url"] == (
        "http://judge.test/v1"
    )


# --- operator only, PIN once ------------------------------------------------------------------


async def test_an_agent_cannot_approve_a_batch(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    ids = await _queue_settings(client)
    response = await client.post(ROUTE, json={"approval_ids": ids}, headers=AGENT)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "AGENT_CANNOT_DECIDE"
    assert {r["status"] for r in _rows(ids).values()} == {"pending"}


async def test_the_settings_pin_is_asked_once_for_the_whole_batch(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    token = await _set_pin(client)
    ids = await _queue_settings(client)

    refused = await client.post(ROUTE, json={"approval_ids": ids})
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "PIN_REQUIRED"
    assert {r["status"] for r in _rows(ids).values()} == {"pending"}

    approved = await client.post(ROUTE, json={"approval_ids": ids}, headers={HEADER: token})
    assert approved.status_code == 200, approved.text
    assert [r["status"] for r in approved.json()["results"]] == ["executed"] * 3


# --- which requests count as settings changes --------------------------------------------------


async def test_the_listing_flags_settings_changes_from_the_live_routes(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    ids = await _queue_settings(client)
    other = await _queue_other("version_delete", "src.api.v1.endpoints.versions.delete_version")
    token_import = await _queue_other("secret_write", "src.api.v1.endpoints.sources.preview_hf")
    listed = {
        a["id"]: a["settings_change"]
        for a in (await client.get("/api/v1/approvals?status=pending")).json()["approvals"]
    }
    assert listed == {ids[0]: True, ids[1]: True, ids[2]: True, other: False, token_import: False}
    assert (await client.get(f"/api/v1/approvals/{ids[0]}")).json()["settings_change"] is True


def test_the_settings_change_targets_are_the_pin_gated_executors() -> None:
    from src.main import fastapi_app

    targets = settings_pin.settings_change_targets(fastapi_app.routes)
    assert targets == {
        "src.api.v1.endpoints.settings.put_setting",
        "src.api.v1.endpoints.settings.delete_setting",
        "src.api.v1.endpoints.endpoint_roles.put_role",
    }


def test_the_batch_route_is_registered_in_the_live_app() -> None:
    from src.main import fastapi_app

    paths = fastapi_app.openapi()["paths"]
    assert "post" in paths[ROUTE]


# --- a pre-existing race, fixed in the touched code (2026-10-29) --------------------------------


async def test_approve_rereads_a_row_this_session_already_holds(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    """The session keeps objects across commits; a row read before the lock (the route's action
    check, the batch's pre-check) must not keep its stale ``pending`` once another decision has
    landed, or the request runs a second time."""
    from src.core.database import async_session_factory
    from src.core.errors import ConflictError
    from src.services.approval_service import ApprovalService

    ids = await _queue_settings(client)
    async with async_session_factory()() as db:
        held = await ApprovalService.get(db, ids[0])
        assert held.status == "pending"
        with get_sync_engine().begin() as conn:
            conn.execute(
                text("UPDATE dw_approvals SET status = 'rejected' WHERE id = :i"), {"i": ids[0]}
            )
        with pytest.raises(ConflictError):
            await ApprovalService.approve(db, ids[0], "Test Operator")
    assert _setting("log_level") is None
