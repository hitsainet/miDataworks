"""``GET /api/health`` reports each dependency and never raises (Foundation task 3.7)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from src.core.config import get_settings
from src.services import health_service


async def test_all_up_is_200_ok(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    for name in ("postgres", "redis", "data_volume"):
        assert body["dependencies"][name] == {**body["dependencies"][name], "ok": True}
    assert body["dependencies"]["millm"]["configured"] is False
    assert body["dependencies"]["mistudio"]["reason"] == "not configured"
    resources = body["resources"]
    assert resources["memory_total_bytes"] > 0 and resources["disk_total_bytes"] > 0


async def test_redis_down_is_reported_not_raised(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "redis_url", "redis://127.0.0.1:1/0")
    response = await client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["dependencies"]["redis"]["ok"] is False
    assert "Redis" in body["dependencies"]["redis"]["reason"]
    assert body["dependencies"]["postgres"]["ok"] is True


async def test_postgres_down_is_reported_not_raised(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken() -> dict[str, Any]:
        raise AssertionError("must not be called")

    class _Engine:
        def connect(self) -> Any:
            raise ConnectionRefusedError("down")

    monkeypatch.setattr(health_service, "get_async_engine", lambda: _Engine())
    body = (await client.get("/api/health")).json()
    assert body["status"] == "degraded"
    assert body["dependencies"]["postgres"] == {
        "ok": False,
        "reason": "ConnectionRefusedError: cannot query the database",
    }


async def test_unwritable_data_volume_is_reported(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    blocker = tmp_path / "file-not-dir"
    blocker.write_text("x")
    monkeypatch.setattr(get_settings(), "data_dir", blocker)
    body = (await client.get("/api/health")).json()
    assert body["dependencies"]["data_volume"]["ok"] is False
    assert body["status"] == "degraded"


def _fake_http(handler: Any) -> Any:
    real = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    return factory


async def test_millm_and_mistudio_reachable(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"status": "ok"})

    monkeypatch.setattr(get_settings(), "millm_base_url", "http://millm.test")
    monkeypatch.setattr(get_settings(), "mistudio_base_url", "http://mistudio.test/")
    monkeypatch.setattr(health_service.httpx, "AsyncClient", _fake_http(handler))
    body = (await client.get("/api/health")).json()
    assert body["dependencies"]["millm"]["ok"] is True
    assert body["dependencies"]["mistudio"]["ok"] is True
    assert sorted(seen) == ["http://millm.test/api/health", "http://mistudio.test/api/health"]


async def test_millm_unreachable_and_mistudio_erroring_are_reported(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "millm.test":
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(503)

    monkeypatch.setattr(get_settings(), "millm_base_url", "http://millm.test")
    monkeypatch.setattr(get_settings(), "mistudio_base_url", "http://mistudio.test")
    monkeypatch.setattr(health_service.httpx, "AsyncClient", _fake_http(handler))
    response = await client.get("/api/health")
    assert response.status_code == 200
    deps = response.json()["dependencies"]
    assert deps["millm"]["ok"] is False and "unreachable" in deps["millm"]["reason"]
    assert deps["mistudio"] == {**deps["mistudio"], "ok": False, "reason": "answered 503"}
    # Other apps being down does not degrade miDataworks itself.
    assert response.json()["status"] == "ok"
