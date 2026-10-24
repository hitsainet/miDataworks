"""The Settings PIN (feature 014; operator directive 2026-10-20).

Server-side enforcement on every Settings write, derived from the LIVE OpenAPI document: every
write route under ``/api/v1/settings`` and ``/api/v1/endpoint-roles`` is either PIN-gated (and each
one is driven here: 403 without a session, through with one) or exempt with a reason. Removing
the ``dependencies=[SETTINGS_PIN]`` line from any gated route turns its case red (controls in
``0xcc/reviews/014_implementation_controls_2026-10-20.md``).

Agents: their requests pass the PIN and stop at the approval gate (or are refused); an approved
request then executes WITHOUT the PIN, while the operator's approval of a Settings change needs it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from src.core import settings_pin
from src.core.config import env_settings
from src.core.database import get_sync_engine

PIN = "4711"
NEW_PIN = "135792"
AGENT = {"X-Dataworks-Agent": "agent:dataworks-mcp"}
HEADER = settings_pin.SESSION_HEADER

Call = Callable[[httpx.AsyncClient, dict[str, str]], Awaitable[httpx.Response]]

#: Every PIN-gated Settings write, with a request that would succeed (or fail for another reason)
#: once unlocked. The live route set must equal GATED | EXEMPT (test below).
GATED: dict[tuple[str, str], Call] = {
    ("PUT", "/api/v1/settings/{key}"): lambda c, h: c.put(
        "/api/v1/settings/operator_name", json={"value": "Ada"}, headers=h
    ),
    ("DELETE", "/api/v1/settings/{key}"): lambda c, h: c.delete(
        "/api/v1/settings/operator_name", headers=h
    ),
    ("PUT", "/api/v1/settings/shortcut-level"): lambda c, h: c.put(
        "/api/v1/settings/shortcut-level", json={"margin_pp": 7.0, "reason": "pin"}, headers=h
    ),
    ("PUT", "/api/v1/endpoint-roles/{role}"): lambda c, h: c.put(
        "/api/v1/endpoint-roles/judge",
        json={"protocol": "openai_chat", "base_url": "http://judge.test/v1"},
        headers=h,
    ),
    ("DELETE", "/api/v1/endpoint-roles/{role}/keys"): lambda c, h: c.delete(
        "/api/v1/endpoint-roles/judge/keys", params={"origin": "http://judge.test"}, headers=h
    ),
}

EXEMPT: dict[tuple[str, str], str] = {
    ("POST", "/api/v1/endpoint-roles/{role}/test"): "a connection test; it changes nothing",
    ("PUT", "/api/v1/settings/pin"): "needs the CURRENT PIN in its body instead",
    ("POST", "/api/v1/settings/pin/session"): "the unlock itself takes the PIN",
    ("POST", "/api/v1/settings/pin/remove"): "needs the CURRENT PIN in its body instead",
}

WRITE_METHODS = {"put", "post", "delete", "patch"}
PREFIXES = ("/api/v1/settings", "/api/v1/endpoint-roles")


@pytest.fixture(autouse=True)
def _fresh_env_settings() -> Iterator[None]:
    env_settings.cache_clear()
    yield
    env_settings.cache_clear()


async def _set_pin(client: httpx.AsyncClient, pin: str = PIN) -> str:
    response = await client.put("/api/v1/settings/pin", json={"pin": pin})
    assert response.status_code == 200, response.text
    return str(response.json()["token"])


def _stored_hash() -> str | None:
    with get_sync_engine().connect() as conn:
        return conn.execute(text("SELECT pin_hash FROM dw_settings_pin")).scalar_one_or_none()


def _setting(key: str) -> str | None:
    with get_sync_engine().connect() as conn:
        return conn.execute(
            text("SELECT value FROM dw_app_settings WHERE key = :k"), {"k": key}
        ).scalar_one_or_none()


# --- the route set ------------------------------------------------------------------------------


def test_every_settings_write_route_is_gated_or_exempt_with_a_reason() -> None:
    from src.main import fastapi_app

    live = {
        (method.upper(), path)
        for path, item in fastapi_app.openapi()["paths"].items()
        if path.startswith(PREFIXES)
        for method in item
        if method in WRITE_METHODS
    }
    assert live == set(GATED) | set(EXEMPT), (
        "a Settings write route is neither PIN-gated here nor exempt with a reason: "
        f"{sorted(live ^ (set(GATED) | set(EXEMPT)))}"
    )


@pytest.mark.parametrize("route", sorted(GATED), ids=lambda r: f"{r[0]} {r[1]}")
async def test_each_gated_route_refuses_without_a_session_and_passes_with_one(
    client: httpx.AsyncClient, operator_name: str, route: tuple[str, str]
) -> None:
    call = GATED[route]
    token = await _set_pin(client)
    before = _setting("operator_name")

    for headers in (
        {},
        {HEADER: "not-a-token"},
        {HEADER: token[:-1] + ("1" if token.endswith("0") else "0")},
    ):
        refused = await call(client, headers)
        assert refused.status_code == 403, (route, headers, refused.text)
        assert refused.json()["error"]["code"] == "PIN_REQUIRED"
    assert _setting("operator_name") == before, "a refused write changed nothing"

    unlocked = await call(client, {HEADER: token})
    assert unlocked.status_code != 403, unlocked.text
    assert unlocked.status_code < 500


async def test_with_no_pin_settings_are_open(client: httpx.AsyncClient, operator_name: str) -> None:
    status = (await client.get("/api/v1/settings/pin")).json()
    assert status["configured"] is False and status["bypass_active"] is False
    for call in GATED.values():
        assert (await call(client, {})).status_code != 403


# --- set, unlock, change, remove ------------------------------------------------------------------


async def test_the_pin_is_stored_hashed(client: httpx.AsyncClient, operator_name: str) -> None:
    token = await _set_pin(client)
    stored = _stored_hash()
    assert stored is not None and PIN not in stored
    assert stored.startswith("pbkdf2_sha256$600000$")
    assert settings_pin.verify_pin(PIN, stored) and not settings_pin.verify_pin("0000", stored)
    status = (await client.get("/api/v1/settings/pin", headers={HEADER: token})).json()
    assert status == {
        "configured": True,
        "bypass_active": False,
        "session_valid": True,
        "session_ttl_s": settings_pin.SESSION_TTL_S,
        "header": HEADER,
    }
    assert stored not in (await client.get("/api/v1/settings/pin")).text


async def test_unlock_answers_a_session_for_the_right_pin_only(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    await _set_pin(client)
    wrong = await client.post("/api/v1/settings/pin/session", json={"pin": "0000"})
    assert wrong.status_code == 403 and wrong.json()["error"]["code"] == "PIN_WRONG"
    right = await client.post("/api/v1/settings/pin/session", json={"pin": PIN})
    assert right.status_code == 200 and right.json()["header"] == HEADER
    put = await client.put(
        "/api/v1/settings/operator_name",
        json={"value": "Grace"},
        headers={HEADER: right.json()["token"]},
    )
    assert put.status_code == 200 and _setting("operator_name") == "Grace"


async def test_unlock_without_a_pin_set(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/settings/pin/session", json={"pin": PIN})
    assert response.status_code == 409 and response.json()["error"]["code"] == "PIN_NOT_SET"


async def test_changing_the_pin_needs_the_current_one_and_ends_old_sessions(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    old_token = await _set_pin(client)
    missing = await client.put("/api/v1/settings/pin", json={"pin": NEW_PIN})
    assert missing.status_code == 403 and missing.json()["error"]["code"] == "PIN_CURRENT_REQUIRED"
    wrong = await client.put("/api/v1/settings/pin", json={"pin": NEW_PIN, "current_pin": "9999"})
    assert wrong.status_code == 403 and wrong.json()["error"]["code"] == "PIN_WRONG"
    assert settings_pin.verify_pin(PIN, _stored_hash() or "")

    changed = await client.put("/api/v1/settings/pin", json={"pin": NEW_PIN, "current_pin": PIN})
    assert changed.status_code == 200
    assert settings_pin.verify_pin(NEW_PIN, _stored_hash() or "")
    stale = await GATED[("PUT", "/api/v1/settings/{key}")](client, {HEADER: old_token})
    assert stale.status_code == 403, "a session of the old PIN must end with it"
    fresh = await GATED[("PUT", "/api/v1/settings/{key}")](
        client, {HEADER: changed.json()["token"]}
    )
    assert fresh.status_code == 200


async def test_removing_the_pin_needs_the_current_one(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    await _set_pin(client)
    wrong = await client.post("/api/v1/settings/pin/remove", json={"current_pin": "1111"})
    assert wrong.status_code == 403 and _stored_hash() is not None
    removed = await client.post("/api/v1/settings/pin/remove", json={"current_pin": PIN})
    assert removed.status_code == 200 and removed.json()["configured"] is False
    assert _stored_hash() is None
    assert (await GATED[("PUT", "/api/v1/settings/{key}")](client, {})).status_code == 200


@pytest.mark.parametrize("bad", ["123", "12a4", "1234567890123", "", "１２３４"])
async def test_a_pin_is_four_to_twelve_digits(client: httpx.AsyncClient, bad: str) -> None:
    response = await client.put("/api/v1/settings/pin", json={"pin": bad})
    assert response.status_code == 422 and _stored_hash() is None


async def test_the_history_records_the_pin_changes_and_never_the_pin(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    await _set_pin(client)
    await client.put("/api/v1/settings/pin", json={"pin": NEW_PIN, "current_pin": PIN})
    await client.post("/api/v1/settings/pin/remove", json={"current_pin": NEW_PIN})
    with get_sync_engine().connect() as conn:
        rows = conn.execute(
            text(
                "SELECT previous_value, new_value, actor FROM dw_setting_changes "
                "WHERE key = 'settings_pin' ORDER BY id"
            )
        ).all()
    assert [tuple(r) for r in rows] == [
        (None, "set", "Test Operator"),
        ("set", "changed", "Test Operator"),
        ("set", None, "Test Operator"),
    ]


async def test_wrong_pins_lock_unlocking_for_a_while(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    await _set_pin(client)
    for _ in range(settings_pin.MAX_FAILURES):
        assert (
            await client.post("/api/v1/settings/pin/session", json={"pin": "0000"})
        ).status_code == 403
    locked = await client.post("/api/v1/settings/pin/session", json={"pin": PIN})
    assert locked.status_code == 429 and locked.json()["error"]["code"] == "PIN_LOCKED"


def test_a_session_expires() -> None:
    stored = settings_pin.hash_pin(PIN)
    token, expires = settings_pin.mint_session(stored, now=1_000.0)
    assert expires == 1_000 + settings_pin.SESSION_TTL_S
    assert settings_pin.session_valid(token, stored, now=1_001.0)
    assert not settings_pin.session_valid(token, stored, now=float(expires))
    assert not settings_pin.session_valid(token, settings_pin.hash_pin(PIN), now=1_001.0)


# --- recovery ---------------------------------------------------------------------------------------


async def test_the_bypass_variable_opens_settings_and_resets_the_pin(
    client: httpx.AsyncClient, operator_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _set_pin(client)
    monkeypatch.setenv("SETTINGS_PIN_BYPASS", "true")
    env_settings.cache_clear()
    status = (await client.get("/api/v1/settings/pin")).json()
    assert status["bypass_active"] is True and status["configured"] is True
    for call in GATED.values():
        assert (await call(client, {})).status_code != 403
    reset = await client.put("/api/v1/settings/pin", json={"pin": NEW_PIN})
    assert reset.status_code == 200, "the forgotten PIN is replaced without the current one"
    assert settings_pin.verify_pin(NEW_PIN, _stored_hash() or "")


async def test_the_bypass_cannot_be_switched_on_from_the_app(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    refused = await client.put("/api/v1/settings/settings_pin_bypass", json={"value": "true"})
    assert refused.status_code in (404, 422)
    from src.core import runtime_settings

    assert "settings_pin_bypass" in runtime_settings.NEVER_WRITABLE


# --- agents -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("PUT", "/api/v1/settings/pin", {"pin": PIN}),
        ("POST", "/api/v1/settings/pin/session", {"pin": PIN}),
        ("POST", "/api/v1/settings/pin/remove", {"current_pin": PIN}),
    ],
)
async def test_agents_cannot_use_the_pin(
    client: httpx.AsyncClient, method: str, path: str, body: dict[str, Any]
) -> None:
    response = await client.request(method, path, json=body, headers=AGENT)
    assert response.status_code == 403 and response.json()["error"]["code"] == "AGENT_FORBIDDEN"


async def test_an_agent_write_waits_for_approval_and_the_approved_request_runs_without_the_pin(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    token = await _set_pin(client)
    queued = await client.put(
        "/api/v1/endpoint-roles/judge",
        json={"protocol": "openai_chat", "base_url": "http://judge.test/v1"},
        headers=AGENT,
    )
    assert queued.status_code == 202 and queued.json()["action"] == "config_write"
    approval_id = queued.json()["approval_id"]
    assert (await client.get("/api/v1/endpoint-roles/judge")).json()["base_url"] is None

    # Approving a Settings change is the operator's act, behind the PIN ...
    refused = await client.post(f"/api/v1/approvals/{approval_id}/approve")
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "PIN_REQUIRED"
    assert (await client.get(f"/api/v1/approvals/{approval_id}")).json()["status"] == "pending"
    # ... and the approved request then runs without one (the stored request carries no PIN).
    approved = await client.post(
        f"/api/v1/approvals/{approval_id}/approve", headers={HEADER: token}
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "executed"
    assert (await client.get("/api/v1/endpoint-roles/judge")).json()["base_url"] == (
        "http://judge.test/v1"
    )


async def test_the_approval_executor_needs_no_pin(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    """The service path (what an approval runs) has no request and no PIN: it executes."""
    from src.core.database import async_session_factory
    from src.services.approval_service import ApprovalService

    await _set_pin(client)
    queued = await client.put("/api/v1/settings/log_level", json={"value": "DEBUG"}, headers=AGENT)
    assert queued.status_code == 202
    async with async_session_factory()() as db:
        row = await ApprovalService.approve(db, queued.json()["approval_id"], "Test Operator")
    assert row.status == "executed"
    assert _setting("log_level") == "DEBUG"
