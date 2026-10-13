"""Endpoint roles and Fetch models (ADR-011; Foundation tasks 8.2, 8.3, 8.4)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy import text

from src.clients import model_listing
from src.clients.model_listing import EndpointError, fetch_models
from src.core.database import get_sync_engine
from src.core.encryption import decrypt_value

KEY = "sk-judge-key-0123456789abcdef"


def _ciphertext(role: str) -> str | None:
    with get_sync_engine().connect() as conn:
        return conn.execute(
            text("SELECT api_key_ciphertext FROM dw_endpoint_roles WHERE role = :r"), {"r": role}
        ).scalar()


async def test_four_roles_and_no_list(client: httpx.AsyncClient) -> None:
    roles = (await client.get("/api/v1/endpoint-roles")).json()
    assert [r["role"] for r in roles] == ["classifier", "judge", "generation", "embeddings"]
    assert all(not r["configured"] for r in roles)
    gen = next(r for r in roles if r["role"] == "generation")
    assert gen["inherit_from_judge"] is True


async def test_configure_a_role_with_an_encrypted_key(client: httpx.AsyncClient) -> None:
    response = await client.put(
        "/api/v1/endpoint-roles/judge",
        json={
            "protocol": "openai_chat",
            "base_url": "http://judge.test/v1",
            "model_id": "qwen",
            "api_key": KEY,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["api_key"] == "sk-...cdef" and body["has_api_key"] is True
    assert KEY not in response.text
    assert decrypt_value(_ciphertext("judge") or "") == KEY


async def test_masked_null_and_empty_keys(client: httpx.AsyncClient) -> None:
    base = {"protocol": "openai_chat", "base_url": "http://judge.test/v1", "model_id": "q"}
    await client.put("/api/v1/endpoint-roles/judge", json={**base, "api_key": KEY})
    before = _ciphertext("judge")
    await client.put("/api/v1/endpoint-roles/judge", json={**base, "api_key": "sk-...cdef"})
    assert _ciphertext("judge") == before, "a masked key must leave the ciphertext byte-identical"
    await client.put("/api/v1/endpoint-roles/judge", json={**base, "api_key": None})
    assert _ciphertext("judge") == before, "null leaves the stored key"
    await client.put("/api/v1/endpoint-roles/judge", json={**base, "api_key": ""})
    assert _ciphertext("judge") is None, "an empty string clears it"


@pytest.mark.parametrize(
    ("role", "body", "code"),
    [
        ("teacher", {}, "UNKNOWN_ROLE"),
        ("judge", {"protocol": "tei_classification"}, "UNKNOWN_PROTOCOL"),
        ("classifier", {"inherit_from_judge": True}, "INHERIT_NOT_ALLOWED"),
        ("classifier", {"use_mode": "none"}, "USE_MODE_NOT_ALLOWED"),
        ("judge", {"base_url": "ftp://x"}, "BAD_URL"),
    ],
)
async def test_invalid_role_writes_are_refused(
    client: httpx.AsyncClient, role: str, body: dict[str, Any], code: str
) -> None:
    response = await client.put(f"/api/v1/endpoint-roles/{role}", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == code


async def test_inheritance(client: httpx.AsyncClient) -> None:
    await client.put(
        "/api/v1/endpoint-roles/classifier",
        json={"protocol": "openai_scoring", "base_url": "http://millm.test/v1", "model_id": "jev"},
    )
    await client.put("/api/v1/endpoint-roles/judge", json={"use_mode": "same_as_classifier"})
    roles = {r["role"]: r for r in (await client.get("/api/v1/endpoint-roles")).json()}
    assert roles["judge"]["effective_role"] == "classifier"
    assert roles["generation"]["effective_role"] == "classifier"  # via the judge
    await client.put("/api/v1/endpoint-roles/judge", json={"use_mode": "none"})
    roles = {r["role"]: r for r in (await client.get("/api/v1/endpoint-roles")).json()}
    assert (
        roles["judge"]["effective_role"] is None and roles["embeddings"]["effective_role"] is None
    )


async def test_an_agent_key_write_waits_for_approval(client: httpx.AsyncClient) -> None:
    headers = {"X-Dataworks-Agent": "agent:dataworks-mcp"}
    gated = await client.put("/api/v1/endpoint-roles/judge", json={"api_key": KEY}, headers=headers)
    assert gated.status_code == 202 and gated.json()["action"] == "secret_write"
    assert _ciphertext("judge") is None
    ungated = await client.put(
        "/api/v1/endpoint-roles/judge", json={"base_url": "http://judge.test/v1"}, headers=headers
    )
    assert ungated.status_code == 200


# --- Fetch models (task 8.3) -------------------------------------------------------------------


def _transport(
    routes: dict[str, httpx.Response | Exception], seen: list[httpx.Request]
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        result = routes.get(request.url.path)
        if result is None:
            return httpx.Response(404)
        if isinstance(result, Exception):
            raise result
        return result

    return httpx.MockTransport(handler)


async def test_openai_shape() -> None:
    seen: list[httpx.Request] = []
    routes = {
        "/v1/models": httpx.Response(
            200, json={"object": "list", "data": [{"id": "a"}, {"id": "b"}]}
        )
    }
    listing = await fetch_models("http://millm.test/v1", "sk-k", transport=_transport(routes, seen))
    assert listing.models == ["a", "b"] and listing.source == "openai"
    assert seen[0].headers["Authorization"] == "Bearer sk-k"
    assert seen[0].headers["X-miLLM-Strict"] == "true"


async def test_tei_shape_when_v1_models_is_absent() -> None:
    seen: list[httpx.Request] = []
    routes = {"/info": httpx.Response(200, json={"model_id": "deberta-v3-humor", "model_type": {}})}
    listing = await fetch_models("http://tei.test", None, transport=_transport(routes, seen))
    assert listing.models == ["deberta-v3-humor"] and listing.source == "tei"
    assert [r.url.path for r in seen] == ["/v1/models", "/info"]
    assert "Authorization" not in seen[0].headers


async def test_tei_shape_when_v1_models_is_not_openai_shaped() -> None:
    routes = {
        "/v1/models": httpx.Response(200, json={"models": ["x"]}),
        "/info": httpx.Response(200, json={"model_id": "deberta"}),
    }
    listing = await fetch_models("http://tei.test/v1/", None, transport=_transport(routes, []))
    assert listing.source == "tei"


@pytest.mark.parametrize(
    ("routes", "code"),
    [
        ({"/v1/models": httpx.ConnectError("refused")}, "ENDPOINT_UNREACHABLE"),
        ({"/v1/models": httpx.ReadTimeout("slow")}, "ENDPOINT_UNREACHABLE"),
        ({"/v1/models": httpx.Response(401)}, "ENDPOINT_UNAUTHORIZED"),
        ({"/v1/models": httpx.Response(403)}, "ENDPOINT_UNAUTHORIZED"),
        (
            {
                "/v1/models": httpx.Response(
                    200, text="<html>ingress</html>", headers={"content-type": "text/html"}
                )
            },
            "ENDPOINT_NOT_JSON",
        ),
        ({"/v1/models": httpx.Response(500)}, "ENDPOINT_ERROR"),
        ({}, "ENDPOINT_UNRECOGNISED"),
        ({"/info": httpx.Response(200, json={"nothing": 1})}, "ENDPOINT_UNRECOGNISED"),
    ],
)
async def test_each_failure_has_its_own_code(routes: dict[str, Any], code: str) -> None:
    with pytest.raises(EndpointError) as excinfo:
        await fetch_models("http://x.test", None, transport=_transport(routes, []))
    assert excinfo.value.code == code


async def test_an_empty_model_list_is_not_a_success() -> None:
    routes = {"/v1/models": httpx.Response(200, json={"data": []})}
    with pytest.raises(EndpointError) as excinfo:
        await fetch_models("http://x.test", None, transport=_transport(routes, []))
    assert excinfo.value.code == "ENDPOINT_UNRECOGNISED"


async def test_fetch_models_route_uses_the_stored_key_only_for_the_stored_url(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str | None]] = []

    async def fake(base_url: str, api_key: str | None, **_: Any) -> model_listing.ModelListing:
        calls.append((base_url, api_key))
        return model_listing.ModelListing(["m"], "openai", base_url + "/models")

    from src.services import endpoint_role_service

    monkeypatch.setattr(endpoint_role_service, "fetch_models", fake)
    await client.put(
        "/api/v1/endpoint-roles/judge",
        json={"protocol": "openai_chat", "base_url": "http://judge.test/v1", "api_key": KEY},
    )
    ok = await client.get("/api/v1/endpoint-roles/judge/models")
    other = await client.get(
        "/api/v1/endpoint-roles/judge/models", params={"base_url": "http://evil.test"}
    )
    assert ok.status_code == other.status_code == 200
    assert calls == [("http://judge.test/v1", KEY), ("http://evil.test", None)]


async def test_fetch_models_on_an_unconfigured_role(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/endpoint-roles/classifier/models")
    assert response.status_code == 422 and response.json()["error"]["code"] == "ROLE_UNCONFIGURED"


async def test_fetch_models_failure_reaches_the_client_as_an_envelope(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get(
        "/api/v1/endpoint-roles/classifier/models", params={"base_url": "http://127.0.0.1:1"}
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "ENDPOINT_UNREACHABLE"
