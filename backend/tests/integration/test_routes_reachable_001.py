"""Every feature 001 route reaches its service with the payload, exactly once (001 FTASKS 14.1).

The registry shape lives in ``tests/unit/test_reachability.py`` (EXPECTED_ROUTES_001, the Celery
tasks, the job kind) and the OpenAPI ``x-approval-action`` shape beside it; this file is the caller
shape. For each route the function it must call is replaced by a spy, the route is called with a
payload, and the spy must have received exactly one call carrying that payload.
"""

from __future__ import annotations

import importlib
import inspect
import json
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, call

import httpx
import pytest

from src.core.database import sync_session_factory
from tests.support import db_factories
from tests.unit.test_reachability import EXPECTED_ROUTES_001, live_routes

SHA = "2bb7d6bce15e42c2a3cf2be8305fa3049929d3ac"


class Stop(Exception):
    """Raised by a spy after recording, so the route returns quickly; the call is what counts."""


#: (method, template, path-with-{id}, body, "module[:Class]", attribute, check(call) -> bool)
CASES: list[tuple[str, str, str, Any, str, str, Callable[[Any], bool]]] = [
    (
        "GET",
        "/api/v1/sources/meta",
        "/api/v1/sources/meta",
        None,
        "src.services.app_setting_service:AppSettingService",
        "get_int",
        lambda c: c.args[1] == "upload_max_bytes",
    ),
    (
        "POST",
        "/api/v1/sources/hf/preview",
        "/api/v1/sources/hf/preview",
        {"repo_id": "org/data", "split": "train", "revision": "v1", "access_token": "hf_spy_tok"},
        "src.api.v1.endpoints.sources",
        "wait_for_preview",
        lambda c: c.args[1]["repo_id"] == "org/data"
        and c.args[1]["split"] == "train"
        and c.args[1]["revision"] == "v1"
        and c.args[1]["token_supplied"] is True
        and "access_token" not in c.args[1]
        and c.args[0].startswith("preview_"),
    ),
    (
        "POST",
        "/api/v1/sources/hf",
        "/api/v1/sources/hf",
        {"repo_id": "org/data", "config": "c", "revision": "main", "confirm_large": True},
        "src.services.job_service:JobService",
        "create",
        lambda c: c.kwargs["kind"] == "source_import"
        and c.kwargs["params"]
        == {
            "mode": "hf",
            "repo_id": "org/data",
            "config": "c",
            "split": None,
            "revision": "main",
            "confirm_large": True,
            "token_supplied": False,
        }
        and c.kwargs["started_by"] == "Test Operator",
    ),
    (
        "POST",
        "/api/v1/sources/uploads",
        "/api/v1/sources/uploads",
        "UPLOAD",
        "src.services.sources.source_service",
        "find_by_content_hash",
        lambda c: len(c.args[1]) == 64,
    ),
    (
        "GET",
        "/api/v1/sources",
        "/api/v1/sources?kind=hf&page=1&limit=10",
        None,
        "src.api.v1.endpoints.sources",
        "summary_of",
        lambda c: SEEN[-1] == ("hf", ID[0]),
    ),
    (
        "GET",
        "/api/v1/sources/{source_id}",
        "/api/v1/sources/{id}",
        None,
        "src.services.sources.source_service",
        "get_source_row",
        lambda c: c.args[1] == ID[0],
    ),
    (
        "GET",
        "/api/v1/sources/{source_id}/rows",
        "/api/v1/sources/{id}/rows?split=train&page=2&limit=20",
        None,
        "src.services.sources.source_service",
        "rows_page",
        lambda c: c.args[1:] == (ID[0], "train", 2, 20),
    ),
    (
        "POST",
        "/api/v1/sources/{source_id}/annotations",
        "/api/v1/sources/{id}/annotations",
        {"kind": "terms", "redistribution": "permits", "reason": "read"},
        "src.services.sources.source_service",
        "annotate",
        lambda c: c.args[1] == ID[0]
        and c.args[2]
        == {"kind": "terms", "redistribution": "permits", "value": {}, "reason": "read"}
        and c.args[3].who == "Test Operator"
        and c.kwargs["approval"] is None,
    ),
    (
        "DELETE",
        "/api/v1/sources/{source_id}",
        "/api/v1/sources/{id}",
        {"reason": "old"},
        "src.services.sources.source_service",
        "delete",
        lambda c: c.args[1:3] == (ID[0], "old") and c.args[3].who == "Test Operator",
    ),
]
ID: list[str] = [""]
#: (kind, id) of the row a spy received, read while its session was still open.
SEEN: list[tuple[Any, Any]] = []


def _snapshot(*args: Any) -> None:
    row = args[1] if len(args) > 1 else None
    SEEN.append((getattr(row, "kind", None), getattr(row, "id", None)))


def test_every_001_route_has_a_caller_case() -> None:
    assert {(m, p) for m, p, *_ in CASES} == set(EXPECTED_ROUTES_001)
    assert set(EXPECTED_ROUTES_001) <= live_routes()


def _target(spec: str) -> Any:
    module_name, _, cls = spec.partition(":")
    module = importlib.import_module(module_name)
    return getattr(module, cls) if cls else module


@pytest.mark.parametrize("case", CASES, ids=[f"{c[0]} {c[1]}" for c in CASES])
async def test_route_calls_its_service_once_with_the_payload(
    client: httpx.AsyncClient,
    operator_name: str,
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[Any, ...],
) -> None:
    method, _, path, body, target_spec, attribute, check = case
    with sync_session_factory()() as s:
        ID[0] = db_factories.source(s).id
        s.commit()
    target = _target(target_spec)
    real = inspect.getattr_static(target, attribute)
    real = real.__func__ if isinstance(real, staticmethod) else real
    spy: Any = AsyncMock(side_effect=_stop) if inspect.iscoroutinefunction(real) else _SyncSpy()
    monkeypatch.setattr(target, attribute, spy)
    path = path.replace("{id}", ID[0])
    if body == "UPLOAD":
        manifest = {"files": [{"name": "a.jsonl", "split": "train"}]}
        response = await client.post(
            path,
            files={"files": ("a.jsonl", b'{"text": "x"}\n')},
            data={"manifest": json.dumps(manifest)},
        )
    else:
        response = await client.request(method, path, json=body)
    assert response.status_code == 500, response.text  # Stop propagated: the spy really ran
    calls = spy.await_args_list if isinstance(spy, AsyncMock) else spy.calls
    assert len(calls) == 1, f"{method} {path} called {attribute} {len(calls)} times"
    assert check(calls[0]), calls[0]


async def _stop(*args: Any, **_: Any) -> Any:
    _snapshot(*args)
    raise Stop("recorded")


class _SyncSpy:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(call(*args, **kwargs))
        raise Stop("recorded")
