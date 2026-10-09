"""Every feature 002 route reaches its service with the payload, exactly once (task 18.1).

The registry and no-strays shapes live in ``tests/unit/test_reachability.py`` (EXPECTED_ROUTES_002);
this file is the caller shape: for each route, the service function the route must call is
replaced by a spy, the route is called with a payload, and the spy must have received exactly one
call carrying that payload. "Was called" alone passes against a call sending the wrong arguments.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from tests.unit.test_reachability import EXPECTED_ROUTES_002, live_routes

DS = "11111111-1111-1111-1111-111111111111"
V = "22222222-2222-2222-2222-222222222222"
REC = "33333333-3333-3333-3333-333333333333"
REV = "44444444-4444-4444-4444-444444444444"
DRAFT = "55555555-5555-5555-5555-555555555555"
BODY = {
    "format": "dw.recipe/v1",
    "steps": [{"operator": "stub_keep", "version": "1", "params": {}}],
}


class Stop(Exception):
    """Raised by a spy after recording, so the route returns quickly; the call is what counts."""


#: (method, path template, concrete path, json body, module, attribute, check(call) -> bool)
CASES: list[tuple[str, str, str, Any, str, str, Callable[[Any], bool]]] = [
    (
        "GET",
        "/api/v1/datasets/meta",
        "/api/v1/datasets/meta",
        None,
        "dataset_service",
        "meta",
        lambda c: c.args == (),
    ),
    (
        "GET",
        "/api/v1/datasets",
        "/api/v1/datasets?q=hum&target_type=sft",
        None,
        "dataset_service",
        "list_datasets",
        lambda c: c.kwargs["q"] == "hum" and c.kwargs["target_type"] == "sft",
    ),
    (
        "POST",
        "/api/v1/datasets",
        "/api/v1/datasets",
        {"name": "humor", "target_type": "dpo"},
        "dataset_service",
        "create",
        lambda c: c.kwargs["name"] == "humor" and c.kwargs["target_type"] == "dpo",
    ),
    (
        "GET",
        "/api/v1/datasets/{dataset_id}",
        f"/api/v1/datasets/{DS}",
        None,
        "dataset_service",
        "get_dataset_row",
        lambda c: c.args[1] == DS,
    ),
    (
        "PATCH",
        "/api/v1/datasets/{dataset_id}",
        f"/api/v1/datasets/{DS}",
        {"description": "d"},
        "dataset_service",
        "patch",
        lambda c: c.args[1] == DS and c.kwargs["fields"] == {"description": "d"},
    ),
    (
        "GET",
        "/api/v1/recipes",
        "/api/v1/recipes?q=x&archived=true",
        None,
        "recipe_service",
        "list_recipes",
        lambda c: c.kwargs["q"] == "x" and c.kwargs["archived"] is True,
    ),
    (
        "POST",
        "/api/v1/recipes",
        "/api/v1/recipes",
        {"name": "r", "body": BODY},
        "recipe_service",
        "create",
        lambda c: c.kwargs["name"] == "r" and c.kwargs["body"] == BODY,
    ),
    (
        "POST",
        "/api/v1/recipes/validate",
        "/api/v1/recipes/validate",
        {"body": BODY},
        "recipe_service",
        "validate_body",
        lambda c: c.args[0] == BODY,
    ),
    (
        "POST",
        "/api/v1/recipes/import",
        "/api/v1/recipes/import",
        "FILE",
        "recipe_service",
        "import_file",
        lambda c: c.args[2] == b'{"x":1}',
    ),
    (
        "GET",
        "/api/v1/recipes/{recipe_id}",
        f"/api/v1/recipes/{REC}",
        None,
        "recipe_service",
        "get_recipe_row",
        lambda c: c.args[1] == REC,
    ),
    (
        "POST",
        "/api/v1/recipes/{recipe_id}/revisions",
        f"/api/v1/recipes/{REC}/revisions",
        {"body": BODY},
        "recipe_service",
        "revise",
        lambda c: c.args[2] == REC and c.kwargs["body"] == BODY,
    ),
    (
        "POST",
        "/api/v1/recipes/{recipe_id}/clone",
        f"/api/v1/recipes/{REC}/clone",
        {"name": "c"},
        "recipe_service",
        "clone",
        lambda c: c.args[2] == REC and c.kwargs["name"] == "c",
    ),
    (
        "POST",
        "/api/v1/recipes/{recipe_id}/archive",
        f"/api/v1/recipes/{REC}/archive",
        None,
        "recipe_service",
        "archive",
        lambda c: c.args[2] == REC,
    ),
    (
        "GET",
        "/api/v1/recipes/{recipe_id}/revisions/{revision_id}/export",
        f"/api/v1/recipes/{REC}/revisions/{REV}/export",
        None,
        "recipe_service",
        "export",
        lambda c: c.args[1:] == (REC, REV),
    ),
    (
        "POST",
        "/api/v1/recipes/{recipe_id}/build",
        f"/api/v1/recipes/{REC}/build",
        {"dataset_id": DS, "inputs": [{"kind": "source", "source_id": DS}]},
        "recipe_service",
        "get_recipe_row",
        lambda c: c.args[1] == REC,
    ),
    (
        "GET",
        "/api/v1/recipe-drafts",
        f"/api/v1/recipe-drafts?dataset_id={DS}",
        None,
        "draft_service",
        "list_drafts",
        lambda c: c.args[1] == DS,
    ),
    (
        "POST",
        "/api/v1/recipe-drafts",
        "/api/v1/recipe-drafts",
        {"name": "n"},
        "draft_service",
        "create",
        lambda c: c.args[2]["name"] == "n",
    ),
    (
        "GET",
        "/api/v1/recipe-drafts/{draft_id}",
        f"/api/v1/recipe-drafts/{DRAFT}",
        None,
        "draft_service",
        "get",
        lambda c: c.args[1] == DRAFT,
    ),
    (
        "PUT",
        "/api/v1/recipe-drafts/{draft_id}",
        f"/api/v1/recipe-drafts/{DRAFT}",
        {"name": "m"},
        "draft_service",
        "update",
        lambda c: c.args[2] == DRAFT and c.args[3]["name"] == "m",
    ),
    (
        "DELETE",
        "/api/v1/recipe-drafts/{draft_id}",
        f"/api/v1/recipe-drafts/{DRAFT}",
        None,
        "draft_service",
        "delete",
        lambda c: c.args[1] == DRAFT,
    ),
    (
        "POST",
        "/api/v1/recipe-drafts/{draft_id}/save",
        f"/api/v1/recipe-drafts/{DRAFT}/save",
        {"recipe_name": "x"},
        "draft_service",
        "save_as_revision",
        lambda c: c.args[2:] == (DRAFT, "x"),
    ),
    (
        "POST",
        "/api/v1/versions",
        "/api/v1/versions",
        {
            "dataset_id": DS,
            "recipe_revision_id": REV,
            "inputs": [{"kind": "version", "version_id": V}],
            "seed": 3,
        },
        "version_build_service",
        "request_build",
        lambda c: c.args[1].seed == 3 and c.args[1].recipe_revision_id == REV,
    ),
    (
        "GET",
        "/api/v1/versions",
        f"/api/v1/versions?dataset_id={DS}&state=completed",
        None,
        "version_read_service",
        "list_versions",
        lambda c: c.kwargs["dataset_id"] == DS and c.kwargs["state"] == "completed",
    ),
    (
        "GET",
        "/api/v1/versions/{version_id}",
        f"/api/v1/versions/{V}",
        None,
        "version_read_service",
        "version_out",
        lambda c: c.args[1] == V,
    ),
    (
        "GET",
        "/api/v1/versions/{version_id}/manifest",
        f"/api/v1/versions/{V}/manifest",
        None,
        "version_read_service",
        "get_version_row",
        lambda c: c.args[1] == V,
    ),
    (
        "GET",
        "/api/v1/versions/{version_id}/rows",
        f"/api/v1/versions/{V}/rows?split=train&q=pun",
        None,
        "lineage_service",
        "rows_page",
        lambda c: c.args[1] == V and c.kwargs["split"] == "train" and c.kwargs["query"] == "pun",
    ),
    (
        "GET",
        "/api/v1/versions/{version_id}/rows/history",
        f"/api/v1/versions/{V}/rows/history?q=pun",
        None,
        "lineage_service",
        "history",
        lambda c: c.args[1:] == (V, None, "pun"),
    ),
    (
        "GET",
        "/api/v1/versions/{version_id}/drop-log",
        f"/api/v1/versions/{V}/drop-log",
        None,
        "lineage_service",
        "drop_log",
        lambda c: c.args[1] == V,
    ),
    (
        "GET",
        "/api/v1/versions/{version_id}/events",
        f"/api/v1/versions/{V}/events?step_index=2&kind=dropped",
        None,
        "lineage_service",
        "events_page",
        lambda c: c.args[1] == V and c.kwargs["step_index"] == 2 and c.kwargs["kind"] == "dropped",
    ),
    (
        "GET",
        "/api/v1/versions/{version_id}/lineage",
        f"/api/v1/versions/{V}/lineage",
        None,
        "lineage_service",
        "lineage",
        lambda c: c.args[1] == V,
    ),
    (
        "GET",
        "/api/v1/versions/{version_id}/compare",
        f"/api/v1/versions/{V}/compare?with={DS}",
        None,
        "compare_service",
        "compare",
        lambda c: c.args[1:] == (V, DS),
    ),
    (
        "POST",
        "/api/v1/versions/{version_id}/verify-rebuild",
        f"/api/v1/versions/{V}/verify-rebuild",
        None,
        "version_delete_service",
        "request_verify",
        lambda c: c.args[2] == V,
    ),
    (
        "DELETE",
        "/api/v1/versions/{version_id}",
        f"/api/v1/versions/{V}",
        {"reason": "old"},
        "version_delete_service",
        "delete",
        lambda c: c.args[2:] == (V, "old"),
    ),
]


def test_every_002_route_has_a_caller_case() -> None:
    assert {(m, p) for m, p, *_ in CASES} == set(EXPECTED_ROUTES_002)
    assert set(EXPECTED_ROUTES_002) <= live_routes()


@pytest.mark.parametrize("case", CASES, ids=[f"{c[0]} {c[1]}" for c in CASES])
async def test_route_calls_its_service_once_with_the_payload(
    client: httpx.AsyncClient,
    operator_name: str,
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[Any, ...],
) -> None:
    import importlib

    method, _, path, body, module_name, attribute, check = case
    module = importlib.import_module(f"src.services.{module_name}")
    real = getattr(module, attribute)
    spy = AsyncMock(side_effect=Stop("recorded")) if _is_async(real) else _SyncSpy()
    monkeypatch.setattr(module, attribute, spy)
    if body == "FILE":
        response = await client.post(path, files={"file": ("r.json", b'{"x":1}')})
    else:
        response = await client.request(method, path, json=body)
    assert response.status_code == 500, response.text  # Stop propagated: the spy really ran
    calls = spy.await_args_list if isinstance(spy, AsyncMock) else spy.calls
    assert len(calls) == 1, f"{method} {path} called {attribute} {len(calls)} times"
    assert check(calls[0]), calls[0]


def _is_async(fn: Any) -> bool:
    import inspect

    return inspect.iscoroutinefunction(fn)


class _SyncSpy:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        from unittest.mock import call

        self.calls.append(call(*args, **kwargs))
        raise Stop("recorded")
