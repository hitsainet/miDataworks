"""An endpoint role still on a previous miServer address is flagged, never moved silently
(feature 020; FR-020.14; ID-8; FTASKS 6.13).

The role keeps working (R1 resumes, TPD-8 withholds a key bound to the old origin from the new
one); the screen and health say which roles to re-point, and where."""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest

from src.core.config import get_settings
from tests.support.labeling_fixtures import set_role

OLD = "http://millm-backend.millm.test:8000"
NEW = "http://miserver-backend.miserver.test:8000"


@pytest.fixture
def moved(monkeypatch: pytest.MonkeyPatch, clean_db: None) -> Iterator[None]:
    settings = get_settings()
    monkeypatch.setattr(settings, "millm_base_url", NEW)
    monkeypatch.setattr(settings, "miserver_previous_base_urls", (OLD,))
    yield


async def _role(client: httpx.AsyncClient, role: str) -> dict[str, object]:
    response = await client.get(f"/api/v1/endpoint-roles/{role}")
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


async def test_role_on_previous_origin_is_flagged(moved: None, client: httpx.AsyncClient) -> None:
    set_role("judge", base_url=OLD + "/v1", protocol="openai_chat")
    set_role("classifier", base_url="http://other.test:8000/v1")
    judge = await _role(client, "judge")
    assert judge["uses_previous_address"] is True
    assert judge["current_address"] == NEW + "/v1"
    other = await _role(client, "classifier")
    assert other["uses_previous_address"] is False and other["current_address"] is None
    listed = {r["role"]: r for r in (await client.get("/api/v1/endpoint-roles")).json()}
    assert listed["judge"]["uses_previous_address"] is True


async def test_flag_clears_after_put_to_current(moved: None, client: httpx.AsyncClient) -> None:
    set_role("judge", base_url=OLD + "/v1", protocol="openai_chat")
    put = await client.put(
        "/api/v1/endpoint-roles/judge",
        json={"protocol": "openai_chat", "base_url": NEW + "/v1", "model_id": "JEV-9B-decision"},
    )
    assert put.status_code == 200, put.text
    judge = await _role(client, "judge")
    assert judge["uses_previous_address"] is False and judge["current_address"] is None


async def test_key_is_not_sent_to_the_new_host(moved: None, client: httpx.AsyncClient) -> None:
    """TPD-8: the key stays bound to the old origin; the flag names the new address only."""
    set_role("judge", base_url=OLD + "/v1", protocol="openai_chat")
    judge = await _role(client, "judge")
    assert judge["key_origin"] == "http://millm-backend.millm.test:8000"
    from src.core.key_binding import key_for

    assert key_for(str(judge["current_address"]), "sk-x", str(judge["key_origin"])) is None


async def test_health_lists_roles_on_previous_address(
    moved: None, client: httpx.AsyncClient
) -> None:
    set_role("judge", base_url=OLD + "/v1", protocol="openai_chat")
    body = (await client.get("/api/health")).json()
    assert body["dependencies"]["millm"]["roles_on_previous_address"] == [
        {"role": "judge", "base_url": OLD + "/v1", "current_address": NEW + "/v1"}
    ]
