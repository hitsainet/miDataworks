"""Clone a proven setup to a new behaviour key and read what changed (017 FTASKS 11.8; FR-017.59).

Through MCP over the real backend, for BOTH fixture behaviours: the behaviour's carrier-plan recipe
and its generation template are cloned to a new key with substitutions
(``dataworks_clone_recipe``, ``dataworks_clone_generation_template``); the structured difference
(``dataworks_get_clone_difference``) lists exactly the substituted pointers and the key; a pointer
that names nothing is refused with its next step and saves nothing; and a build from the cloned
recipe carries the new key on every row and in its recorded definition and configuration.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pyarrow.parquet as pq
import pytest
from sqlalchemy import func, select

from src.core.database import sync_session_factory
from src.models.recipe import Recipe
from tests.integration.behaviour.test_carrier_plan_build import index_source
from tests.integration.test_version_build import request
from tests.support.behaviour_fixtures import BEHAVIOURS, INDEX_ROLES, definition, plan_params
from tests.support.mcp_live import call, live_mcp
from tests.support.operator_build_driver import RealDriver


def plan_body(key: str, rows: int) -> dict[str, Any]:
    return {
        "format": "dw.recipe/v1",
        "steps": [
            {"operator": "carrier_plan", "version": "1.0.0", "params": plan_params(key, rows)}
        ],
    }


@pytest.mark.parametrize("key", BEHAVIOURS)
async def test_a_clone_differs_only_where_it_was_told_and_builds_under_its_key(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str, key: str
) -> None:
    new_key = f"{key}-clone"
    mcp, mcp_client = live_mcp()
    try:
        recipe = await call(
            mcp,
            "dataworks_save_recipe",
            {"name": f"{key}-plan", "body": plan_body(key, 80), "behaviour_key": key},
        )
        subs = [
            {"pointer": "/steps/0/params/definition", "value": json.dumps(definition(new_key))},
            {"pointer": "/steps/0/params/rows", "value": 60},
        ]
        clone = await call(
            mcp,
            "dataworks_clone_recipe",
            {
                "recipe_id": recipe["recipe_id"],
                "name": f"{new_key}-plan",
                "behaviour_key": new_key,
                "substitutions": subs,
            },
        )
        assert clone["behaviour_key"] == new_key
        diff = await call(
            mcp,
            "dataworks_get_clone_difference",
            {"kind": "recipe", "a": recipe["recipe_id"], "b": clone["id"]},
        )
        changed = {d["path"]: (d["op"], d.get("new")) for d in diff["difference"]}
        assert changed == {
            "/behaviour_key": ("replace", new_key),
            "/body/steps/0/params/definition": ("replace", subs[0]["value"]),
            "/body/steps/0/params/rows": ("replace", 60),
        }, changed
        assert diff["b"]["cloned_from_revision_id"] == recipe["revision_id"]

        # a template: the next version under the new key, its prompt substituted
        template = await call(
            mcp,
            "dataworks_save_generation_template",
            {
                "name": f"{key}-carrier",
                "kind": "respond",
                "body": {"prompt": "Write about {prompt}."},
                "behaviour_key": key,
            },
        )
        copied = await call(
            mcp,
            "dataworks_clone_generation_template",
            {
                "template_id": template["id"],
                "behaviour_key": new_key,
                "substitutions": [{"pointer": "/prompt", "value": "Write briefly about {prompt}."}],
            },
        )
        tdiff = await call(
            mcp,
            "dataworks_get_clone_difference",
            {"kind": "generation_template", "a": template["id"], "b": copied["id"]},
        )
        assert {d["path"] for d in tdiff["difference"]} == {"/behaviour_key", "/body/prompt"}

        # a pointer that names nothing: refused with its next step, nothing saved
        with sync_session_factory()() as db:
            before = db.execute(select(func.count()).select_from(Recipe)).scalar_one()
        bad = await client.post(
            f"/api/v1/recipes/{recipe['recipe_id']}/clone",
            json={"name": "bad", "substitutions": [{"pointer": "/steps/9/params", "value": 1}]},
        )
        assert bad.status_code == 422, bad.text
        error = bad.json()["error"]
        assert error["code"] == "substitution_pointer_invalid"
        assert error["details"]["next"]["tool"] == "dataworks_get_recipe"
        with sync_session_factory()() as db:
            assert db.execute(select(func.count()).select_from(Recipe)).scalar_one() == before
    finally:
        await mcp_client.close()

    # the clone builds under its own key, on every row
    dataset = await client.post(
        "/api/v1/datasets", json={"name": f"{new_key}-ds", "target_type": "detector"}
    )
    assert dataset.status_code == 201, dataset.text
    source = index_source(data_dir, 60, f"{new_key}-idx")
    response = await request(
        client,
        dataset.json()["id"],
        clone["head_revision_id"],
        [{"kind": "source", "source_id": source}],
        seed=7,
        column_roles=INDEX_ROLES,
    )
    assert response.status_code == 202, response.text
    assert driver.run(response.json()["job_id"]) == "completed"
    with sync_session_factory()() as db:
        from src.models.version import VersionBuild

        built = db.get(VersionBuild, response.json()["job_id"])
        assert built is not None and built.version_id is not None
        version_id = str(built.version_id)
    version = (await client.get(f"/api/v1/versions/{version_id}")).json()
    assert version["behaviour_definition"]["key"] == new_key
    assert version["behaviour_configuration"]["behaviour_key"] == new_key
    train = next(s for s in version["splits"] if s["name"] == "train")
    keys = set(
        pq.read_table(data_dir / train["path"], columns=["behaviour_key"]).column(0).to_pylist()
    )
    assert keys == {new_key} and train["rows"] == 60
