"""A malformed id in a path or filter is a client error, never a 500 (005 17.4 live finding).

On the deployed stack (2026-10-18), ``GET /api/v1/label-runs?input_version_id=2c817685`` (a short
id) reached the database as a UUID bind parameter, asyncpg raised ``DataError: invalid UUID``, and
the API answered ``500 INTERNAL_ERROR``. The same sweep found eight more routes with the bug.

Two layers:

- **The sweep** reads the live OpenAPI registry. Every operation is called with a short id in all
  its path parameters (and an empty JSON body where it takes one), and every query parameter is
  given a short id in turn (path ids then a well-formed UUID). None may answer 5xx. An operation
  whose body has required fields answers 422 before its handler runs; the rest reach their
  handlers. Derived from the registry, so a route added later is covered the day it lands.
- **The explicit cases** pin each fixed route's answer: a filter refuses a short id with the
  standard ``422 VALIDATION_ERROR`` envelope naming the parameter and answers 200 on a UUID; a path
  id answers ``404 version_not_found`` like ``GET /versions/{id}`` and its other sub-routes.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest

from src.main import fastapi_app
from tests.integration.labeling.helpers import setup_classifier, start_body
from tests.support.labeling_fixtures import Labeling, labeling

__all__ = ["labeling"]

SHORT = "2c817685"
VALID = "6f1c2a3b-0000-4000-8000-000000000001"

#: (path, filter) for every list filter that answered 500 on a short id before the fix.
FIXED_FILTERS = [
    ("/api/v1/label-runs", "input_version_id"),
    ("/api/v1/generation-runs", "input_version_id"),
    ("/api/v1/minimal-pair-chains", "input_version_id"),
    ("/api/v1/calibration-sets", "version_id"),
    ("/api/v1/review-queues", "version_id"),
    ("/api/v1/exports", "version_id"),
    ("/api/v1/publishes", "version_id"),
]

#: (method, path template) for every path id that answered 500 on a short id before the fix.
FIXED_PATHS = [
    ("GET", "/api/v1/versions/{}/diversity"),
    ("POST", "/api/v1/versions/{}/diversity"),
    ("POST", "/api/v1/versions/{}/shortcut-audit"),
]


def operations() -> list[tuple[str, str, list[dict[str, Any]]]]:
    out = []
    for path, ops in fastapi_app.openapi()["paths"].items():
        for method, op in ops.items():
            if method in {"get", "delete", "post", "put", "patch"}:
                out.append((method.upper(), path, op.get("parameters", [])))
    return out


def sweep_cases() -> list[tuple[str, str, dict[str, str]]]:
    cases = []
    for method, path, params in operations():
        path_params = [p["name"] for p in params if p["in"] == "path"]
        # Every query parameter, not only ``*_id``: ``with`` (compare) and ``snapshot`` hold ids
        # too, and a short id where a kind or a state is expected must not 500 either.
        queries = [p["name"] for p in params if p["in"] == "query"]
        if path_params:
            concrete = path
            for name in path_params:
                concrete = concrete.replace("{" + name + "}", SHORT)
            cases.append((method, concrete, {}))
        for name in queries:
            concrete = path
            for p in path_params:
                concrete = concrete.replace("{" + p + "}", VALID)
            cases.append((method, concrete, {name: SHORT}))
    return cases


def test_the_sweep_reaches_every_route_fixed_here() -> None:
    """The sweep is not vacuous: each route this file fixes is one of its cases."""
    cases = sweep_cases()
    for path, name in FIXED_FILTERS:
        assert ("GET", path, {name: SHORT}) in cases, path
    for method, template in FIXED_PATHS:
        assert (method, template.format(SHORT), {}) in cases, template
    assert len(cases) > 200


@pytest.mark.parametrize(("method", "path", "query"), sweep_cases())
async def test_a_short_id_never_answers_5xx(
    client: httpx.AsyncClient, operator_name: str, method: str, path: str, query: dict[str, str]
) -> None:
    body = {} if method in {"POST", "PUT", "PATCH"} else None
    r = await client.request(method, path, params=query, json=body)
    assert r.status_code < 500, (r.status_code, r.text[:300])


@pytest.mark.parametrize(("path", "name"), FIXED_FILTERS)
async def test_a_short_id_filter_is_a_validation_error(
    client: httpx.AsyncClient, path: str, name: str
) -> None:
    r = await client.get(path, params={name: SHORT})
    assert r.status_code == 422, r.text
    error = r.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert [e["loc"] for e in error["details"]["errors"]] == [["query", name]]


@pytest.mark.parametrize(("path", "name"), FIXED_FILTERS)
async def test_a_uuid_filter_is_accepted(client: httpx.AsyncClient, path: str, name: str) -> None:
    r = await client.get(path, params={name: VALID})
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 0


@pytest.mark.parametrize(("method", "template"), FIXED_PATHS)
async def test_a_short_path_id_is_version_not_found(
    client: httpx.AsyncClient, operator_name: str, method: str, template: str
) -> None:
    body = {} if method == "POST" else None
    r = await client.request(method, template.format(SHORT), json=body)
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "version_not_found"


async def test_the_filter_matches_any_spelling_of_the_uuid(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    """The typed filter is canonicalised before the query: an upper-case or hyphenless spelling
    of a version's id finds its run, and another UUID finds none."""
    version_id, template_id = await setup_classifier(client, labeling, n=3)
    run = (await client.post("/api/v1/label-runs", json=start_body(version_id, template_id))).json()
    for spelling in (version_id, version_id.upper(), uuid.UUID(version_id).hex):
        listing = await client.get("/api/v1/label-runs", params={"input_version_id": spelling})
        assert listing.status_code == 200, listing.text
        assert [r["id"] for r in listing.json()["items"]] == [run["id"]], spelling
    other = await client.get("/api/v1/label-runs", params={"input_version_id": VALID})
    assert other.json()["items"] == []
