"""Settings, masking and identity (ADR-015, C5; Foundation tasks 8.1, 8.4, 8.7).

Mutation control (8.7 list): write the masked value back over the ciphertext — the byte-identity
test must turn red.
"""

from __future__ import annotations

import httpx
from sqlalchemy import text

from src.core.database import get_sync_engine
from src.core.encryption import decrypt_value

TOKEN = "hf_RealTokenValueForTests0123456789"
AGENT = {"X-Dataworks-Agent": "agent:dataworks-mcp"}


def _stored(key: str) -> str | None:
    with get_sync_engine().connect() as conn:
        return conn.execute(
            text("SELECT value FROM dw_app_settings WHERE key = :k"), {"k": key}
        ).scalar()


async def test_a_secret_is_stored_encrypted_and_read_masked(client: httpx.AsyncClient) -> None:
    response = await client.put("/api/v1/settings/hf_token", json={"value": TOKEN})
    assert response.status_code == 200
    body = response.json()
    assert body["value"] == "hf_...6789" and body["is_sensitive"] is True
    stored = _stored("hf_token")
    assert stored is not None and TOKEN not in stored and decrypt_value(stored) == TOKEN
    listing = (await client.get("/api/v1/settings")).text
    single = (await client.get("/api/v1/settings/hf_token")).text
    assert TOKEN not in listing and TOKEN not in single


async def test_writing_the_masked_value_back_leaves_the_ciphertext_byte_identical(
    client: httpx.AsyncClient,
) -> None:
    await client.put("/api/v1/settings/hf_token", json={"value": TOKEN})
    before = _stored("hf_token")
    masked = (await client.get("/api/v1/settings/hf_token")).json()["value"]
    response = await client.put("/api/v1/settings/hf_token", json={"value": masked})
    assert response.status_code == 200
    assert _stored("hf_token") == before
    assert decrypt_value(before or "") == TOKEN


async def test_a_masked_value_with_nothing_stored_is_refused(client: httpx.AsyncClient) -> None:
    response = await client.put("/api/v1/settings/hf_token", json={"value": "hf_...6789"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "MASKED_VALUE_WITHOUT_SECRET"


async def test_a_new_value_replaces_the_secret(client: httpx.AsyncClient) -> None:
    await client.put("/api/v1/settings/hf_token", json={"value": TOKEN})
    await client.put("/api/v1/settings/hf_token", json={"value": "hf_SecondToken000000000000"})
    assert decrypt_value(_stored("hf_token") or "") == "hf_SecondToken000000000000"


async def test_an_unknown_key_is_refused(client: httpx.AsyncClient) -> None:
    response = await client.put("/api/v1/settings/not_a_setting", json={"value": "x"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "UNKNOWN_SETTING"


async def test_hub_default_private_is_not_a_setting(client: httpx.AsyncClient) -> None:
    """The setting was declared, listed to the UI and agents, writable, and read by nothing: a
    ``false`` changed no publish. FR-008.2 is the rule (omitted visibility is private), so the
    setting is gone rather than wired. A write is refused like any unknown key, and the list no
    longer offers it — even when an old row is still in the table."""
    with get_sync_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO dw_app_settings (key, value, is_sensitive, category) "
                "VALUES ('hub_default_private', 'false', false, 'publishing')"
            )
        )
    listing = (await client.get("/api/v1/settings")).json()
    assert "hub_default_private" not in {s["key"] for s in listing}
    for response in (
        await client.put("/api/v1/settings/hub_default_private", json={"value": "false"}),
        await client.get("/api/v1/settings/hub_default_private"),
    ):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "UNKNOWN_SETTING"


def test_every_declared_setting_is_one_the_app_reads() -> None:
    """The registry is pinned so a new key names its reader here, in review.

    - operator_name: core/agent_origin.py (identity for runs and decisions)
    - hf_token: workers/secrets.py (imports and publishes)
    - every runtime environment key (feature 012): read through ``get_settings()`` wherever the
      field is read; ``tests/unit/test_runtime_settings.py`` proves each field has a reader.
      ``detector_hub_namespace`` became ``publish_default_namespace`` (migration 0033).
    """
    from src.core.runtime_settings import RUNTIME_KEYS
    from src.services.app_setting_service import SETTINGS

    assert set(SETTINGS) == {"operator_name", "hf_token", *RUNTIME_KEYS}


# --- operator_name (C5, task 8.7) -------------------------------------------------------------


async def test_operator_name_set_and_read(client: httpx.AsyncClient) -> None:
    response = await client.put("/api/v1/settings/operator_name", json={"value": "  Ada Lovelace "})
    assert response.status_code == 200 and response.json()["value"] == "Ada Lovelace"
    assert (await client.get("/api/v1/settings/operator_name")).json()["value"] == "Ada Lovelace"
    assert _stored("operator_name") == "Ada Lovelace"


async def test_an_empty_operator_name_refuses_identity_actions(client: httpx.AsyncClient) -> None:
    await client.put("/api/v1/settings/operator_name", json={"value": "   "})
    response = await client.post("/api/v1/jobs/selftest", json={})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "NO_IDENTITY" and error["details"] == {"setting": "operator_name"}


async def test_an_agent_cannot_read_or_write_operator_name(client: httpx.AsyncClient) -> None:
    await client.put("/api/v1/settings/operator_name", json={"value": "Ada"})
    write = await client.put(
        "/api/v1/settings/operator_name", json={"value": "Mallory"}, headers=AGENT
    )
    read = await client.get("/api/v1/settings/operator_name", headers=AGENT)
    delete = await client.delete("/api/v1/settings/operator_name", headers=AGENT)
    for response in (write, read, delete):
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "AGENT_FORBIDDEN"
    listing = (await client.get("/api/v1/settings", headers=AGENT)).json()
    assert "operator_name" not in {s["key"] for s in listing}
    assert _stored("operator_name") == "Ada"


async def test_an_agent_secret_write_waits_for_approval(client: httpx.AsyncClient) -> None:
    response = await client.put("/api/v1/settings/hf_token", json={"value": TOKEN}, headers=AGENT)
    assert response.status_code == 202
    body = response.json()
    assert body["action"] == "secret_write" and body["status"] == "pending"
    assert _stored("hf_token") is None, "nothing is written before the operator approves"
    approval = (await client.get(f"/api/v1/approvals/{body['approval_id']}")).json()
    assert TOKEN not in str(approval)
    with get_sync_engine().connect() as conn:
        row = conn.execute(text("SELECT payload::text, secret_payload FROM dw_approvals")).one()
    assert TOKEN not in row[0] and row[1] is not None and TOKEN not in row[1]


async def test_an_agent_non_secret_write_waits_for_config_write(client: httpx.AsyncClient) -> None:
    """Before feature 012 an agent's non-secret write ran at once; ENH-003 D5 gates it."""
    response = await client.put(
        "/api/v1/settings/upload_max_bytes", json={"value": "1048576"}, headers=AGENT
    )
    assert response.status_code == 202 and response.json()["action"] == "config_write"
    assert _stored("upload_max_bytes") is None


async def test_delete_a_secret(client: httpx.AsyncClient) -> None:
    await client.put("/api/v1/settings/hf_token", json={"value": TOKEN})
    assert (await client.delete("/api/v1/settings/hf_token")).status_code == 204
    assert _stored("hf_token") is None
    assert (await client.delete("/api/v1/settings/hf_token")).status_code == 404


async def test_the_storage_limits_are_integer_settings_with_env_defaults(
    client: httpx.AsyncClient,
) -> None:
    """001 FTASKS 12.2 (T-02): upload_max_bytes and import_confirm_bytes. Since feature 012 they
    are runtime environment settings: env default, override in force at once, typed refusal."""
    from src.core.config import get_settings

    views = {s["key"]: s for s in (await client.get("/api/v1/settings")).json()}
    for key in ("upload_max_bytes", "import_confirm_bytes"):
        assert views[key]["category"] == "environment" and views[key]["type"] == "int"
        assert views[key]["source"] == "env" and views[key]["runtime_writable"] is True
    assert get_settings().upload_max_bytes == 2 * 1024**3
    assert get_settings().import_confirm_bytes == 50 * 1000**3
    for bad in ("abc", "0", "-5", "1.5"):
        refused = await client.put("/api/v1/settings/upload_max_bytes", json={"value": bad})
        assert refused.status_code == 422, bad
        assert refused.json()["error"]["code"] == "INVALID_SETTING_VALUE"
    assert (
        await client.put("/api/v1/settings/import_confirm_bytes", json={"value": "12345"})
    ).status_code == 200
    assert get_settings().import_confirm_bytes == 12345
