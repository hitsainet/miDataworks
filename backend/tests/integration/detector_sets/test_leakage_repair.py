"""The leakage repair D-4 names, end to end (PII run F-4, 2026-10-20).

D-4 refused the run's first set over pairs crossing roles and pointed to a UI view no MCP tool
reaches; the run rebuilt everything. Here: a set whose OOD evaluation shares 20 rows with the
training split is refused by D-4; ``POST /detector-sets/{id}/leakage-repair/plan`` keeps the
evaluation whole and returns a ``drop_leakage_pairs`` recipe for the training version; that
recipe is saved and BUILT with the real executor; a set over the repaired version is green on D-4.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from src.operators import registry as registry_module
from src.operators.registry import OperatorRegistry
from src.services import operator_port
from tests.support.detector_fixtures import headline_negatives, labelled_rows, set_body, version
from tests.support.operator_build_driver import RealDriver, real_driver

__all__ = ["real_driver"]
API = "/api/v1"


def _outcome(checks: dict[str, Any], code: str) -> dict[str, Any]:
    return next(o for o in checks["outcomes"] if o["code"] == code)


TRAIN_ROWS = labelled_rows(200, seed=1, tag="tr")


async def _leaking(client: httpx.AsyncClient, data_dir: Path, driver: RealDriver) -> dict[str, str]:
    """The training version is BUILT from a source (so its rows carry every system column and
    can be built from again); 20 OOD rows are copies of training rows."""
    from tests.integration.test_version_build import request, setup, src
    from tests.support.stub_operators import body
    from tests.support.version_fixtures import make_source

    source = make_source(
        data_dir, {"train": TRAIN_ROWS, "test": labelled_rows(60, seed=2, tag="te")}
    )
    ds, rev = await setup(client, body(("fx_keep_all", {})), name="humor-balanced")
    job = (await request(client, ds, rev, [src(source)], seed=1)).json()["job_id"]
    assert driver.run(job) == "completed"
    train = (await client.get(f"{API}/versions", params={"dataset_id": ds})).json()["items"][0]
    ood_rows = labelled_rows(120, seed=3, tag="ood", pairs=True)
    for i in range(20):  # each copy keeps its own pair id
        ood_rows[i] = {**copy.deepcopy(TRAIN_ROWS[i]), "pair_id": ood_rows[i]["pair_id"]}
    ood = version({"test": ood_rows}, dataset_name="humicroedit")
    cal = version({"train": headline_negatives(150, seed=5)}, dataset_name="headline-negatives")
    return {"train": train["id"], "ood": ood, "cal": cal}


async def test_d4_names_the_repair_and_the_repair_turns_it_green(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    real_driver: RealDriver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    leaking = await _leaking(client, data_dir, real_driver)
    reg = OperatorRegistry.build(catalogues=(), entry_points=())  # natives: the operator is one
    monkeypatch.setattr(operator_port, "_registry", reg)
    monkeypatch.setattr(registry_module, "_process", reg)

    created = (await client.post(f"{API}/detector-sets", json=set_body(leaking))).json()
    checks = (await client.post(f"{API}/detector-sets/{created['id']}/checks")).json()
    d4 = _outcome(checks, "D-4")
    assert d4["outcome"] == "refused"
    assert "dataworks_plan_leakage_repair" in d4["next_step"]

    response = await client.post(f"{API}/detector-sets/{created['id']}/leakage-repair/plan")
    assert response.status_code == 200, response.text
    plan = response.json()
    (repair,) = plan["repairs"]
    assert repair["role"] == "train" and repair["version_id"] == leaking["train"]
    assert repair["rows_to_drop"] == 20
    assert [k["role"] for k in repair["keeps_whole"]] == ["ood_eval"]
    step = repair["recipe_body"]["steps"][0]
    assert step["operator"] == "drop_leakage_pairs"
    assert len(step["params"]["pairs_sha256"]) == 64

    saved = await client.post(
        f"{API}/recipes", json={"name": "repair-train", "body": repair["recipe_body"]}
    )
    assert saved.status_code == 201, saved.text
    dataset_id = (await client.get(f"{API}/versions/{leaking['train']}")).json()["dataset_id"]
    started = await client.post(
        f"{API}/versions",
        json={
            "dataset_id": dataset_id,
            "recipe_revision_id": saved.json()["head_revision_id"],
            "inputs": [{"kind": "version", "version_id": leaking["train"]}],
            "seed": 1,
        },
    )
    assert started.status_code == 202, started.text
    assert real_driver.run(started.json()["job_id"]) == "completed"
    versions = (await client.get(f"{API}/versions", params={"dataset_id": dataset_id})).json()
    repaired_id = next(v["id"] for v in versions["items"] if v["id"] != leaking["train"])
    repaired = (await client.get(f"{API}/versions/{repaired_id}")).json()
    counts = {s["name"]: s["rows"] for s in repaired["splits"]}
    assert counts["train"] == 180 and counts["test"] == 60

    fixed = {**leaking, "train": repaired["id"]}
    again = (
        await client.post(f"{API}/detector-sets", json=set_body(fixed, name="repaired"))
    ).json()
    checks = (await client.post(f"{API}/detector-sets/{again['id']}/checks")).json()
    assert _outcome(checks, "D-4")["outcome"] == "green", _outcome(checks, "D-4")


async def test_a_clean_set_has_nothing_to_repair(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    from tests.support.detector_fixtures import humor_versions

    created = (await client.post(f"{API}/detector-sets", json=set_body(humor_versions()))).json()
    plan = (await client.post(f"{API}/detector-sets/{created['id']}/leakage-repair/plan")).json()
    assert plan["repairs"] == [] and plan["crossing_pairs"] == 0


async def test_a_repair_whose_pairs_file_changed_is_refused(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    real_driver: RealDriver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The recipe pins the pairs file by SHA-256: a file that is not the one planned refuses the
    step, so a repair can never drop rows chosen by a different report."""
    leaking = await _leaking(client, data_dir, real_driver)
    reg = OperatorRegistry.build(catalogues=(), entry_points=())
    monkeypatch.setattr(operator_port, "_registry", reg)
    monkeypatch.setattr(registry_module, "_process", reg)
    created = (await client.post(f"{API}/detector-sets", json=set_body(leaking))).json()
    plan = (await client.post(f"{API}/detector-sets/{created['id']}/leakage-repair/plan")).json()
    recipe = copy.deepcopy(plan["repairs"][0]["recipe_body"])
    recipe["steps"][0]["params"]["pairs_sha256"] = "0" * 64
    saved = (await client.post(f"{API}/recipes", json={"name": "bad", "body": recipe})).json()
    dataset_id = (await client.get(f"{API}/versions/{leaking['train']}")).json()["dataset_id"]
    started = await client.post(
        f"{API}/versions",
        json={
            "dataset_id": dataset_id,
            "recipe_revision_id": saved["head_revision_id"],
            "inputs": [{"kind": "version", "version_id": leaking["train"]}],
            "seed": 1,
        },
    )
    job_id = started.json()["job_id"]
    assert real_driver.run(job_id) == "failed"
    job = (await client.get(f"{API}/jobs/{job_id}")).json()
    assert "pairs_changed" in json.dumps(job)
