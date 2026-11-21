"""Rule-set pins move as a group in the upgrade plan (operator decision ENH-006 D50).

016's upgrade plan listed a move only for ``rule_set@1`` steps; 017's ``insert_element@1`` and
``derive_labels@1`` pin rule sets too, so a recipe pinning one name in two kinds of step could be
moved half-way. The plan now moves EVERY pin of one name to that name's latest version together, and
refuses (``409 rule_set_pins_diverge``) a plan that would leave them on different versions.
"""

from __future__ import annotations

import copy
from typing import Any

import httpx
import pytest

from tests.integration.rules.conftest import assert_refusal, fixture_document, saved

pytestmark = pytest.mark.usefixtures("operator_name")
PLAN = "/api/v1/operators/upgrade-plan"


def _pin(name: str, version: int, sha: str) -> dict[str, Any]:
    return {"rule_set_name": name, "rule_set_version": version, "rule_set_sha256": sha}


def _steps(name: str, rs_version: int, rs_sha: str, dl_version: int, dl_sha: str) -> list[Any]:
    return [
        {
            "operator": "rule_set",
            "version": "1",
            "params": {**_pin(name, rs_version, rs_sha), "use": "label", "text_column": "text"},
        },
        {
            "operator": "derive_labels",
            "version": "1.0.0",
            "params": {**_pin(name, dl_version, dl_sha), "label_column": "label"},
        },
    ]


async def _two_versions(client: httpx.AsyncClient) -> tuple[dict[str, Any], dict[str, Any]]:
    document = fixture_document("fixture-beta")
    v1 = await saved(client, document)
    edited = copy.deepcopy(document)
    edited["description"] = "Version two."
    v2 = await saved(client, edited)
    return v1, v2


async def test_every_pin_of_one_name_moves_to_the_same_version(client: httpx.AsyncClient) -> None:
    v1, v2 = await _two_versions(client)
    steps = _steps(v1["name"], 1, v1["sha256"], 2, v2["sha256"])
    plan = await client.post(PLAN, json={"recipe_body": {"format": "dw.recipe/v1", "steps": steps}})
    assert plan.status_code == 200, plan.text
    first, second = plan.json()["steps"]
    assert first["rule_set"]["to"] == {"name": v1["name"], "version": 2, "sha256": v2["sha256"]}
    # the derive_labels pin is part of the plan too (016 left it out), already on v2
    assert second["rule_set"]["from"]["version"] == 2 and second["rule_set"]["to"] is None


async def test_a_plan_that_would_leave_the_pins_apart_is_refused(
    client: httpx.AsyncClient,
) -> None:
    v1, _ = await _two_versions(client)
    steps = _steps(v1["name"], 1, v1["sha256"], 9, "0" * 64)  # v9 is not stored: it cannot move
    plan = await client.post(PLAN, json={"recipe_body": {"format": "dw.recipe/v1", "steps": steps}})
    details = await assert_refusal(
        plan, 409, "rule_set_pins_diverge", name=v1["name"], steps=[1, 2]
    )
    assert details["versions"] == {"1": 2, "2": 9}
