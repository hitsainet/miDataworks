"""The internal emit route is unreachable through the ingress prefix (ADR-008; task 6.3).

The ingress (``k8s/base/ingress.yaml``) routes ``/api/`` to the backend and everything else to
the frontend. The emit route lives outside ``/api/``, so it cannot be reached from outside the
cluster. These tests pin the routing rule in code; the manifest half (every backend-routed path
under ``/api``, and ``/internal`` routed at a Service with no pods) is
``tests/unit/test_k8s_manifests.py::TestIngress``.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from src.api import internal
from src.core.websocket import SOCKETIO_PATH, valid_room
from src.main import fastapi_app
from src.workers.emit import INTERNAL_EMIT_PATH

PUBLIC_PREFIX = "/api/"
FRONTEND = Path(__file__).resolve().parents[3] / "frontend"


def test_the_emit_route_is_outside_the_public_prefix() -> None:
    assert INTERNAL_EMIT_PATH == "/internal/ws/emit"
    assert not INTERNAL_EMIT_PATH.startswith(PUBLIC_PREFIX)
    assert internal.router.prefix == internal.INTERNAL_PREFIX == "/internal"


def test_every_public_route_is_under_api() -> None:
    fastapi_app.openapi_schema = None
    for path in fastapi_app.openapi()["paths"]:
        assert path.startswith(PUBLIC_PREFIX), path
    assert SOCKETIO_PATH.startswith(PUBLIC_PREFIX)


def test_the_dev_proxy_forwards_api_only() -> None:
    config = (FRONTEND / "vite.config.ts").read_text()
    proxied = re.findall(r"'(/[a-z]+)':\s*{", config)
    assert proxied == ["/api"], proxied


@pytest.fixture
async def http() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=fastapi_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        yield client


async def test_the_emit_route_requires_the_internal_token(
    http: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    emit = AsyncMock()
    monkeypatch.setattr(internal, "emit_to_room", emit)
    body = {"room": "dataworks/selftest/job_1", "event": "job:progress", "data": {"progress": 5}}
    assert (await http.post("/internal/ws/emit", json=body)).status_code == 403
    wrong = await http.post("/internal/ws/emit", json=body, headers={"X-Internal-Token": "nope"})
    assert wrong.status_code == 403
    assert emit.await_count == 0


async def test_a_valid_emit_reaches_the_room_once_with_its_payload(
    http: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.core.config import get_settings

    emit = AsyncMock()
    monkeypatch.setattr(internal, "emit_to_room", emit)
    token = get_settings().internal_api_secret.get_secret_value()
    body = {"room": "dataworks/selftest/job_1", "event": "job:progress", "data": {"progress": 5}}
    response = await http.post("/internal/ws/emit", json=body, headers={"X-Internal-Token": token})
    assert response.status_code == 204
    assert emit.await_count == 1
    assert emit.await_args.args == ("dataworks/selftest/job_1", "job:progress", {"progress": 5})


@pytest.mark.parametrize(
    ("room", "ok"),
    [
        ("dataworks/label-runs/job_abc", True),
        ("dataworks/approvals", True),
        ("dataworks/jobs", True),
        ("dataworks/../x", False),
        ("other/selftest/job", False),
        ("dataworks/selftest/", False),
        (5, False),
    ],
)
def test_room_names(room: object, ok: bool) -> None:
    assert valid_room(room) is ok
