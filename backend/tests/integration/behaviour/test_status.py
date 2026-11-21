"""A behaviour's dataset status (017 FTASKS 11.4; FR-017.56, FR-017.57).

Read through MCP over the real backend (``dataworks_get_behaviour_status``), for BOTH fixture
behaviours: an empty key names the first step and every approval ahead; a behaviour part way
through (a rule set saved and a carrier plan built from the behaviour's recipe) names the next
missing step and only the approvals still ahead; adding that object moves the answer; and the read
writes nothing (every table's row count before and after). Nothing here is special to a behaviour.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from src.core.agent_origin import APPROVAL_ACTIONS
from src.core.database import Base, get_sync_engine
from tests.integration.behaviour.test_carrier_plan_build import KEEP, index_source, plan_version
from tests.integration.test_version_build import request, src
from tests.support.behaviour_fixtures import BEHAVIOURS, INDEX_ROLES
from tests.support.label_fixtures import rule_set_document
from tests.support.mcp_live import call, live_mcp
from tests.support.operator_build_driver import RealDriver

#: Every agent request is logged by the middleware (feature 010), whatever the route.
AUDIT = "dw_agent_requests"


def table_counts() -> dict[str, int]:
    import src.models  # noqa: F401 - every table on the metadata

    with get_sync_engine().connect() as conn:
        return {
            name: int(
                conn.execute(text(f'SELECT count(*) FROM "{name}"')).scalar_one()
            )  # noqa: S608
            for name in sorted(Base.metadata.tables)
        }


async def status(key: str) -> dict[str, Any]:
    mcp, mcp_client = live_mcp()
    try:
        before = table_counts()
        answer: dict[str, Any] = await call(
            mcp, "dataworks_get_behaviour_status", {"behaviour_key": key}
        )
        after = table_counts()
        # the one row an agent's request always leaves: the request log (010), not the status
        assert after.pop(AUDIT) == before.pop(AUDIT) + 1
        assert after == before, "the status read wrote a row"
    finally:
        await mcp_client.close()
    return answer


def done(answer: dict[str, Any]) -> list[str]:
    return [s["step"] for s in answer["journey"] if s["done"]]


@pytest.mark.parametrize("key", BEHAVIOURS)
async def test_the_status_names_the_next_step_and_moves_when_it_is_taken(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str, key: str
) -> None:
    empty = await status(key)
    assert empty["behaviour_key"] == key and not empty["complete"]
    assert done(empty) == []
    assert empty["next"]["step"] == "resolve"
    assert empty["next"]["tool"] == "dataworks_resolve_behaviour"
    assert empty["next"]["args"] == {"behaviour_key": key}
    every = {a for s in empty["journey"] for a in s["approvals"]}
    assert set(empty["approvals_ahead"]) == every and every <= set(APPROVAL_ACTIONS)
    assert {"hub_push", "paid_classifier_run"} <= every
    assert all(v == 0 for v in empty["counts"].values()), empty["counts"]

    # part way: the rule set saved, the plan built (it records the definition and configuration)
    saved = await client.post("/api/v1/rule-sets", json={"document": rule_set_document(key)})
    assert saved.status_code == 201, saved.text
    plan = await plan_version(client, driver, data_dir, key, 60)
    part = await status(key)
    # the plan was built from a recipe saved without the key: its recorded configuration
    # carries the key, so the version counts; the recipe step is still open
    assert done(part) == ["resolve", "rule_set", "plan"], done(part)
    assert part["next"]["step"] == "templates"
    assert part["next"]["tool"] == "dataworks_save_generation_template"
    assert part["counts"]["rule_sets"] == 1 and part["counts"]["versions"] == 1
    assert [v["id"] for v in part["objects"]["versions"]] == [plan["id"]]
    assert part["objects"]["rule_sets"][0]["name"] == rule_set_document(key)["name"]
    ahead = set(part["approvals_ahead"])
    assert ahead == {"millm_model_load", "paid_classifier_run", "agent_label_rows", "hub_push"}

    # adding the next object moves the answer to the step after it
    template = await client.post(
        "/api/v1/generation-templates",
        json={"name": f"{key}-t", "kind": "respond", "body": {"prompt": "{prompt}"},
              "behaviour_key": key},
    )  # fmt: skip
    assert template.status_code == 201, template.text
    moved = await status(key)
    assert done(moved) == ["resolve", "rule_set", "templates", "plan"]
    assert moved["next"]["step"] == "recipe"
    assert moved["next"]["tool"] == "dataworks_save_recipe"

    # the behaviour's own recipe, and a version built from it that records no definition: it
    # counts through the recipe's key (the plan and the build make two versions)
    recipe = await client.post(
        "/api/v1/recipes",
        json={
            "name": f"{key}-keep",
            "body": {"format": "dw.recipe/v1", "steps": [KEEP]},
            "behaviour_key": key,
        },
    )
    assert recipe.status_code == 201, recipe.text
    dataset = await client.post(
        "/api/v1/datasets", json={"name": f"{key}-ds", "target_type": "detector"}
    )
    source = index_source(data_dir, 20, f"{key}-status")
    response = await request(
        client,
        dataset.json()["id"],
        recipe.json()["head_revision_id"],
        [src(source)],
        seed=7,
        column_roles=INDEX_ROLES,
    )
    assert response.status_code == 202, response.text
    assert driver.run(response.json()["job_id"]) == "completed"
    built = await status(key)
    assert built["counts"]["versions"] == 2 and built["counts"]["recipes"] == 1
    assert done(built) == ["resolve", "rule_set", "templates", "recipe", "plan", "build"]
    assert built["next"]["step"] == "generation"


async def test_one_behaviours_objects_never_count_for_another(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    alpha, beta = BEHAVIOURS
    saved = await client.post("/api/v1/rule-sets", json={"document": rule_set_document(alpha)})
    assert saved.status_code == 201, saved.text
    await plan_version(client, driver, data_dir, alpha, 40)
    assert done(await status(alpha)) == ["resolve", "rule_set", "plan"]
    other = await status(beta)
    assert done(other) == [] and all(v == 0 for v in other["counts"].values())
