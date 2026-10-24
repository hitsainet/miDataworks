"""Purge, clone, difference and the operator preview of rule sets (FR-016.20, FR-016.21, FTID I14).

- an archived, unused rule set purges (versions, head, tombstone); a used version is refused
  ``409 purge_blocked`` naming the version whose build ran it; a recipe pin blocks too;
- a dataset whose build ran ``rule_set@1`` purges, its use records and step executions with it;
- a clone differs from its source only at the substituted paths and the key; a substitution that
  breaks a case returns that case and saves nothing;
- ``POST /operators/rule_set/1/preview`` runs the pinned stored document.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from src.api.v1.endpoints import operators as operators_endpoint
from src.core.database import get_sync_engine
from src.operators import preview as preview_module
from tests.integration.rules.conftest import RS, assert_refusal, fixture_document, saved
from tests.integration.rules.test_rule_set_build import (
    build_rows,
    case_rows,
    pin,
    recipe,
    rs_step,
)
from tests.support.operator_build_driver import RealDriver
from tests.support.version_fixtures import make_source

pytestmark = pytest.mark.usefixtures("operator_name", "production_driver")
P = "/api/v1/purge"


def _count(table: str, where: str = "TRUE", **params: Any) -> int:
    with get_sync_engine().connect() as conn:
        return int(
            conn.execute(
                text(f"SELECT count(*) FROM {table} WHERE {where}"), params
            ).scalar()  # noqa: S608
        )


# --- purge ------------------------------------------------------------------------------------


async def test_an_archived_unused_rule_set_purges_with_a_tombstone(
    client: httpx.AsyncClient, document: dict[str, Any]
) -> None:
    await saved(client, document)
    edited = copy.deepcopy(document)
    edited["description"] = "Two."
    await saved(client, edited)
    name = document["name"]
    refused = await client.post(f"{P}/rule_set/{name}")
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "not_archived"
    assert (await client.post(f"{RS}/{name}/archive")).status_code == 200
    plan = await client.post(f"{P}/plan", json={"items": [{"kind": "rule_set", "id": name}]})
    assert plan.json()["items"][0]["status"] == "purgeable", plan.text
    out = await client.post(f"{P}/rule_set/{name}", json={"reason": "test data"})
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["status"] == "purged" and body["name"] == name
    assert body["counts"] == {"rule_set_versions": 2, "rule_sets": 1}
    assert _count("dw_config_versions", "name = :n", n=name) == 0
    assert _count("dw_rule_sets", "name = :n", n=name) == 0
    assert _count("dw_purge_events", "entity_kind = 'rule_set' AND entity_id = :n", n=name) == 1


async def test_a_used_version_is_refused_naming_the_version_that_ran_it(
    client: httpx.AsyncClient, production_driver: RealDriver, data_dir: Path
) -> None:
    document = fixture_document("fixture-beta")
    out = await saved(client, document)
    version_id, _, _ = await build_rows(
        client, production_driver, data_dir, recipe(rs_step(out)), case_rows(document), "used"
    )
    assert (await client.post(f"{RS}/{document['name']}/archive")).status_code == 200
    refused = await client.post(f"{P}/rule_set/{document['name']}")
    assert refused.status_code == 409, refused.text
    error = refused.json()["error"]
    assert error["code"] == "purge_blocked"
    tables = {b["table"] for b in error["details"]["blockers"]}
    assert "dw_rule_set_uses" in tables and "dw_recipe_bodies" in tables
    use = next(b for b in error["details"]["blockers"] if b["table"] == "dw_rule_set_uses")
    assert "used v1" in use["message"], use
    assert _count("dw_config_versions", "name = :n", n=document["name"]) == 1
    assert version_id


async def test_a_recipe_pin_alone_blocks_the_purge(client: httpx.AsyncClient) -> None:
    document = fixture_document("fixture-beta")
    out = await saved(client, document)
    created = await client.post(
        "/api/v1/recipes", json={"name": "pins-it", "body": recipe(rs_step(out))}
    )
    assert created.status_code == 201, created.text
    await client.post(f"{RS}/{document['name']}/archive")
    refused = await client.post(f"{P}/rule_set/{document['name']}")
    assert refused.status_code == 409
    (blocker,) = refused.json()["error"]["details"]["blockers"]
    assert blocker["table"] == "dw_recipe_bodies" and "pins-it" in blocker["message"]


async def test_a_dataset_whose_build_ran_a_rule_set_purges_with_its_uses(
    client: httpx.AsyncClient, production_driver: RealDriver, data_dir: Path
) -> None:
    document = fixture_document("fixture-alpha")
    out = await saved(client, document)
    version_id, _, _ = await build_rows(
        client,
        production_driver,
        data_dir,
        recipe(rs_step(out, element_column="element")),
        case_rows(document),
        "gone-with-rules",
    )
    with get_sync_engine().connect() as conn:
        dataset_id, execution = conn.execute(
            text(
                "SELECT v.dataset_id, u.step_execution_id FROM dw_versions v "
                "JOIN dw_version_steps vs ON vs.version_id = v.id "
                "JOIN dw_rule_set_uses u ON u.step_execution_id = vs.step_execution_id "
                "WHERE v.id = CAST(:v AS uuid)"
            ),
            {"v": version_id},
        ).one()
    assert (await client.post(f"/api/v1/datasets/{dataset_id}/archive")).status_code == 200
    purged = await client.post(f"{P}/dataset/{dataset_id}", json={"reason": "test"})
    assert purged.status_code == 200, purged.text
    counts = purged.json()["counts"]
    assert counts["rule_set_uses"] == 1 and counts["step_executions"] >= 2
    assert _count("dw_rule_set_uses") == 0
    assert _count("dw_step_executions", "id = CAST(:e AS uuid)", e=str(execution)) == 0
    # the rule set itself is untouched, and now purgeable once its recipe goes too
    assert _count("dw_config_versions", "kind = 'rule_set'") == 1


# --- clone and difference (FR-016.21) ---------------------------------------------------------


async def test_a_clone_differs_only_at_the_substituted_paths_and_the_key(
    client: httpx.AsyncClient,
) -> None:
    document = fixture_document("fixture-beta")
    await saved(client, document)
    signal = document["signals"][0]
    assert signal["kind"] == "pattern"
    answer = await client.post(
        f"{RS}/{document['name']}/clone",
        json={
            "new_name": "beta-clone",
            "new_behaviour_key": "fixture-alpha",
            "substitutions": [{"pointer": "/description", "value": "A clone."}],
        },
    )
    assert answer.status_code == 201, answer.text
    body = answer.json()
    assert body["name"] == "beta-clone" and body["behaviour_key"] == "fixture-alpha"
    assert body["cloned_from"]["name"] == document["name"]
    assert sorted(d["path"] for d in body["difference"]) == [
        "/behaviour_key",
        "/description",
        "/name",
    ]
    via = await client.get(
        f"{RS}/difference", params={"a": f"{document['name']}@1", "b": "beta-clone@1"}
    )
    assert via.status_code == 200, via.text
    assert via.json()["difference"] == body["difference"]


async def test_a_substitution_that_breaks_a_case_returns_it_and_saves_nothing(
    client: httpx.AsyncClient,
) -> None:
    document = fixture_document("fixture-beta")
    await saved(client, document)
    index = next(i for i, s in enumerate(document["signals"]) if s["id"] == "q")
    before = _count("dw_config_versions")
    args = {
        "new_name": "beta-broken",
        "new_behaviour_key": "fixture-alpha",
        "substitutions": [
            {"pointer": f"/signals/{index}/pattern", "value": "glorpwhistle"},
        ],
    }
    refused = await client.post(f"{RS}/{document['name']}/clone", json=args)
    details = await assert_refusal(refused, 422, "clone_cases_failed")
    failing = {f["case"] for f in details["failing_cases"]}
    assert "one_word" in failing
    assert details["next"]["tool"] == "dataworks_clone_rule_set"
    assert "replacement_cases" in details["next"]["args"]
    assert set(details["replace_case_ids"]) == failing
    assert _count("dw_config_versions") == before
    # supplying replacement cases makes it pass
    texts = {"one_word": "a glorpwhistle thing", "both_words": "zantimere and glorpwhistle"}
    assert failing == set(texts)
    replacements = []
    for case in document["cases"]:
        if case["id"] in failing:
            new = copy.deepcopy(case)
            new["input"] = {"text": texts[case["id"]]}
            replacements.append(new)
    ok = await client.post(
        f"{RS}/{document['name']}/clone", json={**args, "replacement_cases": replacements}
    )
    assert ok.status_code == 201, ok.text


async def test_a_difference_with_a_malformed_reference_is_refused(
    client: httpx.AsyncClient,
) -> None:
    answer = await client.get(f"{RS}/difference", params={"a": "nope", "b": "x@1"})
    assert answer.status_code == 422 and answer.json()["error"]["code"] == "rule_set_invalid"


# --- the operator preview resolves first (FTID I14) ---------------------------------------------


async def test_the_operator_preview_runs_the_pinned_document(
    client: httpx.AsyncClient, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[dict[str, Any]] = []

    def run(req: dict[str, Any], entry: Any) -> None:
        sent.append(req)
        preview_module.run_request(req)

    monkeypatch.setattr(operators_endpoint, "send_preview", run)
    document = fixture_document("fixture-beta")
    out = await saved(client, document)
    source = make_source(data_dir, {"train": case_rows(document)}, repo_id="t/preview")
    response = await client.post(
        "/api/v1/operators/rule_set/1/preview",
        json={
            "params": {**pin(out), "use": "label", "text_column": "text"},
            "input": {"source_id": source},
            "seed": 3,
        },
    )
    assert response.status_code == 200, response.text
    (req,) = sent
    with get_sync_engine().connect() as conn:
        stored = conn.execute(
            text("SELECT body_canonical FROM dw_config_versions WHERE name = :n"),
            {"n": document["name"]},
        ).scalar_one()
    assert req["params"]["rule_set_document"] == stored
