"""A clone's difference by revision id (FU-7a MCP gap (d), 2026-11-27).

Live: ``dataworks_get_clone_difference(kind="recipe", a=<revision id>, b=<revision id>)`` answered
``recipe_not_found``, although ``dataworks_clone_recipe`` takes a ``revision_id``; and with recipe
ids it compared the source's HEAD, right only while the cloned revision was still the head. Here
the source moves on after the clone (revision 2), so the head and the cloned revision differ: a
revision id compares that exact revision, a recipe id still compares the head, and each side of
the answer says which revision it is and whether it was given as a recipe or a revision.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from tests.integration.behaviour.test_clone_difference import plan_body
from tests.support.behaviour_fixtures import BEHAVIOURS
from tests.support.mcp_live import call, live_mcp, refusal_of
from tests.support.operator_build_driver import RealDriver

KEY = BEHAVIOURS[0]


async def _source_moved_on(client: httpx.AsyncClient) -> dict[str, Any]:
    created = await client.post(
        "/api/v1/recipes",
        json={"name": f"{KEY}-rev-src", "body": plan_body(KEY, 80), "behaviour_key": KEY},
    )
    assert created.status_code == 201, created.text
    recipe_id = created.json()["id"]
    first = created.json()["head_revision_id"]
    clone = await client.post(
        f"/api/v1/recipes/{recipe_id}/clone",
        json={
            "name": f"{KEY}-rev-clone",
            "revision_id": first,
            "substitutions": [{"pointer": "/steps/0/params/rows", "value": 60}],
        },
    )
    assert clone.status_code == 201, clone.text
    second = await client.post(
        f"/api/v1/recipes/{recipe_id}/revisions", json={"body": plan_body(KEY, 90)}
    )
    assert second.status_code == 201, second.text
    head = second.json()["id"]
    assert head != first
    return {
        "recipe_id": recipe_id,
        "first": first,
        "head": head,
        "clone_id": clone.json()["id"],
        "clone_revision": clone.json()["head_revision_id"],
    }


async def test_revision_ids_compare_those_exact_revisions(
    client: httpx.AsyncClient, driver: RealDriver, operator_name: str
) -> None:
    ids = await _source_moved_on(client)
    mcp, mcp_client = live_mcp()
    try:
        by_revision = await call(
            mcp,
            "dataworks_get_clone_difference",
            {"kind": "recipe", "a": ids["first"], "b": ids["clone_revision"]},
        )
        changed = {d["path"]: (d.get("old"), d.get("new")) for d in by_revision["difference"]}
        # the cloned revision (80 rows) against the clone (60): the substitution, and only it
        assert changed == {"/body/steps/0/params/rows": (80, 60)}, changed
        a, b = by_revision["a"], by_revision["b"]
        assert (a["id"], a["revision_id"], a["given"]) == (
            ids["recipe_id"],
            ids["first"],
            "revision",
        )
        assert (a["revision_number"], a["is_head"]) == (1, False)
        assert (b["id"], b["revision_id"], b["given"], b["is_head"]) == (
            ids["clone_id"],
            ids["clone_revision"],
            "revision",
            True,
        )
        assert b["cloned_from_revision_id"] == ids["first"]

        # a recipe id still means its head: the source's revision 2 (90 rows), said so
        by_recipe = await call(
            mcp,
            "dataworks_get_clone_difference",
            {"kind": "recipe", "a": ids["recipe_id"], "b": ids["clone_id"]},
        )
        changed = {d["path"]: (d.get("old"), d.get("new")) for d in by_recipe["difference"]}
        assert changed == {"/body/steps/0/params/rows": (90, 60)}, changed
        a = by_recipe["a"]
        assert (a["revision_id"], a["revision_number"], a["given"], a["is_head"]) == (
            ids["head"],
            2,
            "recipe",
            True,
        )

        # a mixed pair: the recipe id's head against an exact revision
        mixed = await call(
            mcp,
            "dataworks_get_clone_difference",
            {"kind": "recipe", "a": ids["head"], "b": ids["first"]},
        )
        changed = {d["path"]: (d.get("old"), d.get("new")) for d in mixed["difference"]}
        assert changed == {"/body/steps/0/params/rows": (90, 80)}, changed
    finally:
        await mcp_client.close()


async def test_an_id_that_is_neither_is_refused_naming_both(
    client: httpx.AsyncClient, driver: RealDriver, operator_name: str
) -> None:
    ids = await _source_moved_on(client)
    missing = "00000000-0000-4000-8000-000000000000"
    mcp, mcp_client = live_mcp()
    try:
        with pytest.raises(Exception) as caught:
            await call(
                mcp,
                "dataworks_get_clone_difference",
                {"kind": "recipe", "a": missing, "b": ids["clone_id"]},
            )
    finally:
        await mcp_client.close()
    code, status, _ = refusal_of(caught.value)
    assert (code, status) == ("recipe_not_found", 404)
    assert "revision" in str(caught.value)
