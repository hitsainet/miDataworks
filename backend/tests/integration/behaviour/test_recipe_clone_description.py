"""A recipe clone's description (dry run, 2026-11-01): a clone named for pass 2 read "D104 test:
...", the source's words. The route takes one, keeps the source's when the request names none, and
clears it on an explicit null. Stored, read back through the real routes."""

from __future__ import annotations

import uuid

import httpx

from tests.integration.behaviour.test_clone_difference import plan_body
from tests.support.behaviour_fixtures import BEHAVIOURS
from tests.support.mcp_live import call, live_mcp
from tests.support.operator_build_driver import RealDriver

API = "/api/v1/recipes"


async def source(
    client: httpx.AsyncClient, description: str | None = "D104 test: the source"
) -> str:
    saved = await client.post(
        API,
        json={
            "name": f"source-{uuid.uuid4().hex[:8]}",
            "description": description,
            "body": plan_body(BEHAVIOURS[0], 80),
        },
    )
    assert saved.status_code == 201, saved.text
    return str(saved.json()["id"])


async def clone(client: httpx.AsyncClient, recipe_id: str, **fields: object) -> httpx.Response:
    payload = {"name": f"clone-{uuid.uuid4().hex[:8]}", **fields}
    return await client.post(f"{API}/{recipe_id}/clone", json=payload)


async def stored(client: httpx.AsyncClient, recipe_id: str) -> str | None:
    got = await client.get(f"{API}/{recipe_id}")
    assert got.status_code == 200, got.text
    description: str | None = got.json()["description"]
    return description


async def test_a_clone_takes_the_description_it_is_given(
    client: httpx.AsyncClient, driver: RealDriver, operator_name: str
) -> None:
    original = await source(client)
    made = await clone(client, original, description="Pass 2 of the merged build")
    assert made.status_code == 201, made.text
    assert await stored(client, made.json()["id"]) == "Pass 2 of the merged build"
    assert await stored(client, original) == "D104 test: the source"  # the source is untouched


async def test_a_clone_keeps_the_sources_description_when_none_is_named(
    client: httpx.AsyncClient, driver: RealDriver, operator_name: str
) -> None:
    made = await clone(client, await source(client))
    assert made.status_code == 201, made.text
    assert await stored(client, made.json()["id"]) == "D104 test: the source"


async def test_an_explicit_null_clears_it(
    client: httpx.AsyncClient, driver: RealDriver, operator_name: str
) -> None:
    made = await clone(client, await source(client), description=None)
    assert made.status_code == 201, made.text
    assert await stored(client, made.json()["id"]) is None


async def test_a_description_over_the_limit_is_refused_like_a_saved_one(
    client: httpx.AsyncClient, driver: RealDriver, operator_name: str
) -> None:
    refused = await clone(client, await source(client), description="x" * 2001)
    assert refused.status_code == 422, refused.text


async def test_the_mcp_tool_sends_it_and_keeps_the_sources_when_omitted(
    client: httpx.AsyncClient, driver: RealDriver, operator_name: str
) -> None:
    """Through the live registry to the real route. The tool must omit an unset description: sent as
    null, it would clear the source's, which the route reads as asked for."""
    original = await source(client)
    mcp, mcp_client = live_mcp()
    try:
        given = await call(
            mcp,
            "dataworks_clone_recipe",
            {"recipe_id": original, "name": "by-agent-given", "description": "Pass 2"},
        )
        omitted = await call(
            mcp, "dataworks_clone_recipe", {"recipe_id": original, "name": "by-agent-omitted"}
        )
    finally:
        await mcp_client.close()
    assert await stored(client, given["id"]) == "Pass 2"
    assert await stored(client, omitted["id"]) == "D104 test: the source"
