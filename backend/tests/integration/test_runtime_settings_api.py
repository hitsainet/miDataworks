"""Runtime environment settings through the real routes (feature 012; ENH-003 §3.3, §4).

The listing and its secrets, never-writable keys by name, validation, the history, and the
agent gate (``config_write``): payload, call count, and nothing changing before approval.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text

from src.core import runtime_settings as rs
from src.core.config import env_settings, get_settings
from src.core.database import get_sync_engine

AGENT = {"X-Dataworks-Agent": "agent:dataworks-mcp"}


def _stored(key: str) -> str | None:
    with get_sync_engine().connect() as conn:
        return conn.execute(
            text("SELECT value FROM dw_app_settings WHERE key = :k"), {"k": key}
        ).scalar()


def _history() -> list[dict[str, object]]:
    with get_sync_engine().connect() as conn:
        rows = conn.execute(
            text(
                "SELECT key, previous_value, new_value, actor, origin, approval_id, reason "
                "FROM dw_setting_changes ORDER BY id"
            )
        ).mappings()
        return [dict(r) for r in rows]


def _approvals() -> int:
    with get_sync_engine().connect() as conn:
        return int(conn.execute(text("SELECT count(*) FROM dw_approvals")).scalar() or 0)


# --- the listing ------------------------------------------------------------------------------


async def test_the_environment_lists_every_field_in_its_group(client: httpx.AsyncClient) -> None:
    body = (await client.get("/api/v1/settings/environment")).json()
    rows = {r["key"]: r for r in body["settings"]}
    assert set(rows) == set(type(env_settings()).model_fields)
    runtime = {k for k, r in rows.items() if r["group"] == "runtime"}
    assert runtime == set(rs.RUNTIME_KEYS)
    assert {k for k, r in rows.items() if r["group"] == "restart"} == set(rs.RESTART_KEYS)
    assert {k for k, r in rows.items() if r["group"] == "approval_rule"} == set(
        rs.APPROVAL_RULE_KEYS
    )
    for key in runtime:
        row = rows[key]
        assert row["runtime_writable"] is True and row["effect"] == rs.RUNTIME_KEYS[key].effect
        assert row["label"] and row["source"] == "env"
    for key, row in rows.items():
        if key not in runtime:
            assert row["runtime_writable"] is False and row["agent_writable"] is False
    assert "pod" in rows["janitor_interval_seconds"]["restart"]
    assert rows["log_level"]["choices"] == ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    assert rows["millm_lease_ttl_seconds"]["maximum"] == 7200
    assert rows["millm_base_url"]["agent_writable"] is False
    assert rows["upload_max_bytes"]["agent_writable"] is True


async def test_no_secret_value_appears_anywhere_in_the_listing(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ENH-003 §4 "Secrets": sentinels in every secret and in both URL passwords; the whole body
    of both listings is searched. Control: ``_shown`` returning the raw value turns this red."""
    await client.get("/api/v1/settings")  # the engine is built before the URL below is shown
    env = env_settings()
    sentinels = {
        "settings_encryption_key": "SENTINEL-ENCRYPTION-KEY-0123456789abcdef",
        "internal_api_secret": "SENTINEL-INTERNAL-SECRET-0123456789",
        "designer_handoff_key": "SENTINEL-DESIGNER-HANDOFF-0123456789",
    }
    for key, value in sentinels.items():
        monkeypatch.setattr(env, key, SecretStr(value))
    monkeypatch.setattr(
        env, "database_url", "postgresql+asyncpg://dw:SENTINELDBPASSWORD@db.test:5432/x_test"
    )
    monkeypatch.setattr(env, "redis_url", "redis://:SENTINELREDISPASSWORD@redis.test:6379/0")
    await client.put("/api/v1/settings/hf_token", json={"value": "hf_SENTINELTOKEN0123456789abcd"})
    for path in ("/api/v1/settings/environment", "/api/v1/settings"):
        body = (await client.get(path)).text
        for needle in (
            *sentinels.values(),
            "SENTINELDBPASSWORD",
            "SENTINELREDISPASSWORD",
            "hf_SENTINELTOKEN0123456789abcd",
        ):
            assert needle not in body, (path, needle)
    rows = {
        r["key"]: r for r in (await client.get("/api/v1/settings/environment")).json()["settings"]
    }
    assert rows["settings_encryption_key"]["is_set"] is True
    assert rows["settings_encryption_key"]["value"] is None
    assert rows["database_url"]["value"] == "postgresql+asyncpg://dw:***@db.test:5432/x_test"
    assert _history() == [], "a secret write is never in the history"


# --- writes -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    sorted(
        rs.NEVER_WRITABLE - {"operator_name"} | set(rs.RESTART_KEYS) | set(rs.APPROVAL_RULE_KEYS)
    ),
)
async def test_never_writable_keys_are_refused_by_name(client: httpx.AsyncClient, key: str) -> None:
    """ENH-003 §4: moving one of these into RUNTIME_KEYS turns its case red."""
    put = await client.put(f"/api/v1/settings/{key}", json={"value": "1"})
    delete = await client.delete(f"/api/v1/settings/{key}")
    for response in (put, delete):
        assert response.status_code == 422, (key, response.text)
        assert response.json()["error"]["code"] == "NOT_RUNTIME_WRITABLE"
    assert _stored(key) is None


async def test_a_write_is_validated_with_the_fields_own_message(client: httpx.AsyncClient) -> None:
    bad = await client.put("/api/v1/settings/millm_lease_ttl_seconds", json={"value": "9000"})
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "INVALID_SETTING_VALUE"
    assert "7200" in bad.json()["error"]["message"]
    cross = await client.put(
        "/api/v1/settings/publish_janitor_limit_seconds", json={"value": "100"}
    )
    assert cross.status_code == 422
    assert "PROGRESS_HEARTBEAT_SECONDS" in cross.json()["error"]["message"]
    assert _stored("publish_janitor_limit_seconds") is None and _history() == []


async def test_a_change_and_a_reset_are_recorded_with_who_and_why(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    response = await client.put(
        "/api/v1/settings/endpoint_http_timeout_seconds",
        json={"value": "90", "reason": "the judge is slow today"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["source"] == "override" and response.json()["value"] == "90.0"
    assert get_settings().endpoint_http_timeout_seconds == 90.0
    reset = await client.delete(
        "/api/v1/settings/endpoint_http_timeout_seconds", params={"reason": "back to normal"}
    )
    assert reset.status_code == 204
    assert (
        get_settings().endpoint_http_timeout_seconds == env_settings().endpoint_http_timeout_seconds
    )
    assert _history() == [
        {
            "key": "endpoint_http_timeout_seconds",
            "previous_value": None,
            "new_value": "90.0",
            "actor": operator_name,
            "origin": "operator",
            "approval_id": None,
            "reason": "the judge is slow today",
        },
        {
            "key": "endpoint_http_timeout_seconds",
            "previous_value": "90.0",
            "new_value": None,
            "actor": operator_name,
            "origin": "operator",
            "approval_id": None,
            "reason": "back to normal",
        },
    ]
    listing = (await client.get("/api/v1/settings/environment")).json()
    assert [c["reason"] for c in listing["history"]] == [
        "back to normal",
        "the judge is slow today",
    ]
    row = next(r for r in listing["settings"] if r["key"] == "endpoint_http_timeout_seconds")
    assert row["updated_by"] == operator_name and row["source"] == "env"


async def test_the_history_is_append_only(client: httpx.AsyncClient) -> None:
    await client.put("/api/v1/settings/upload_max_bytes", json={"value": "4096"})
    with pytest.raises(Exception, match="append-only"):
        with get_sync_engine().begin() as conn:
            conn.execute(text("UPDATE dw_setting_changes SET actor = 'mallory'"))
    with pytest.raises(Exception, match="append-only"):
        with get_sync_engine().begin() as conn:
            conn.execute(text("DELETE FROM dw_setting_changes"))


async def test_a_too_long_reason_is_refused(client: httpx.AsyncClient) -> None:
    response = await client.put(
        "/api/v1/settings/upload_max_bytes", json={"value": "4096", "reason": "x" * 501}
    )
    assert response.status_code == 422 and _stored("upload_max_bytes") is None


# --- agents (D5) ------------------------------------------------------------------------------


async def test_an_agent_change_waits_and_runs_once_on_approve(
    client: httpx.AsyncClient, operator_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.services.app_setting_service import AppSettingService

    spy = AsyncMock(side_effect=AppSettingService.upsert)
    monkeypatch.setattr(AppSettingService, "upsert", spy)
    pending = await client.put(
        "/api/v1/settings/label_transient_retries",
        json={"value": "7", "reason": "flaky network"},
        headers=AGENT,
    )
    assert pending.status_code == 202, pending.text
    body = pending.json()
    assert body["action"] == "config_write" and body["status"] == "pending"
    assert spy.await_count == 0 and _stored("label_transient_retries") is None
    assert get_settings().label_transient_retries == env_settings().label_transient_retries

    approved = await client.post(f"/api/v1/approvals/{body['approval_id']}/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "executed"
    assert spy.await_count == 1
    assert spy.await_args.args[1:] == ("label_transient_retries", "7")
    assert spy.await_args.kwargs["reason"] == "flaky network"
    assert _stored("label_transient_retries") == "7"
    assert get_settings().label_transient_retries == 7
    assert _history() == [
        {
            "key": "label_transient_retries",
            "previous_value": None,
            "new_value": "7",
            "actor": "agent:dataworks-mcp",
            "origin": "agent",
            "approval_id": body["approval_id"],
            "reason": "flaky network",
        }
    ]
    again = await client.post(f"/api/v1/approvals/{body['approval_id']}/approve")
    assert again.status_code == 409 and spy.await_count == 1


async def test_an_agent_reset_waits_and_runs_once_on_approve(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    await client.put("/api/v1/settings/hf_http_timeout_s", json={"value": "45"})
    pending = await client.delete(
        "/api/v1/settings/hf_http_timeout_s", params={"reason": "done"}, headers=AGENT
    )
    assert pending.status_code == 202 and pending.json()["action"] == "config_write"
    assert _stored("hf_http_timeout_s") == "45.0"
    approved = await client.post(f"/api/v1/approvals/{pending.json()['approval_id']}/approve")
    assert approved.json()["status"] == "executed"
    assert _stored("hf_http_timeout_s") is None
    assert get_settings().hf_http_timeout_s == env_settings().hf_http_timeout_s
    last = _history()[-1]
    assert last["new_value"] is None and last["actor"] == "agent:dataworks-mcp"
    assert last["approval_id"] == pending.json()["approval_id"] and last["reason"] == "done"


@pytest.mark.parametrize("key", sorted(k for k, v in rs.RUNTIME_KEYS.items() if v.agent_forbidden))
async def test_operator_only_keys_are_refused_to_agents_before_any_approval(
    client: httpx.AsyncClient, key: str
) -> None:
    put = await client.put(f"/api/v1/settings/{key}", json={"value": "true"}, headers=AGENT)
    delete = await client.delete(f"/api/v1/settings/{key}", headers=AGENT)
    for response in (put, delete):
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "AGENT_FORBIDDEN"
    assert _approvals() == 0


async def test_an_agent_invalid_value_is_refused_before_any_approval(
    client: httpx.AsyncClient,
) -> None:
    response = await client.put(
        "/api/v1/settings/millm_lease_ttl_seconds", json={"value": "0"}, headers=AGENT
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_SETTING_VALUE"
    approval_rule = await client.put(
        "/api/v1/settings/agent_label_row_threshold", json={"value": "999999"}, headers=AGENT
    )
    assert approval_rule.status_code == 422
    assert approval_rule.json()["error"]["code"] == "NOT_RUNTIME_WRITABLE"
    assert _approvals() == 0


async def test_an_agent_reads_the_environment_without_the_operators_name(
    client: httpx.AsyncClient,
) -> None:
    keys = {
        r["key"]
        for r in (await client.get("/api/v1/settings/environment", headers=AGENT)).json()[
            "settings"
        ]
    }
    assert "operator_name" not in keys and "upload_max_bytes" in keys


# --- the MCP tools, in the live registry ------------------------------------------------------


async def test_the_environment_tools_are_in_the_live_registry() -> None:
    """Registered with the server an agent reaches, not merely importable (global rule)."""
    from src.mcp_server.config import MCPSettings
    from src.mcp_server.server import build_server

    server, _ = build_server(MCPSettings(tool_categories="settings", auth_token="x" * 32))
    names = {t.name for t in await server.list_tools()}
    assert {
        "dataworks_get_environment",
        "dataworks_set_environment",
        "dataworks_reset_environment",
    } <= names
