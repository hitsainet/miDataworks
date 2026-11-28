"""A merged build whose runs share carrier ids is refused, loudly, by the real build (dry run,
2026-11-01). Two versions go in as a real merge, through ``POST /versions`` and the real executor; a
green control runs the same recipe over the same rows with one run's ids repaired, so the refusal is
the collision and nothing else about the fixture. The one-plan case (one run per generator, shared
ids and one plan cell) is built for real by ``test_agent_journey.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from src.operators import registry as registry_module
from src.operators.registry import OperatorRegistry
from src.services import operator_port
from tests.support.operator_build_driver import RealDriver, real_driver

__all__ = ["real_driver"]

API = "/api/v1"
ROLES = {
    "text": "content",
    "label": "metadata",
    "genre": "metadata",
    "carrier_id": "metadata",
    "pair_id": "metadata",
    "generation_run_id": "metadata",
}
RECIPE = {
    "format": "dw.recipe/v1",
    "steps": [
        {
            "operator": "split",
            "version": "1.1.0",
            "params": {
                "split_names": ["train", "test"],
                "split_fractions": [0.8, 0.2],
                "held_out": [],
                "group_columns": ["carrier_id", "pair_id"],
            },
        }
    ],
}


def carriers(run: str, ids: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "text": f"{run} wrote carrier {carrier} about something else entirely",
            "label": "pos" if i % 2 else "neg",
            "genre": f"genre-of-{run}",  # two plans: the shared ids are unrelated rows
            "carrier_id": carrier,
            "pair_id": carrier,
            "generation_run_id": run,
        }
        for i, carrier in enumerate(ids)
    ]


async def built(
    client: httpx.AsyncClient,
    driver: RealDriver,
    data_dir: Path,
    dataset: str,
    run: str,
    ids: list[str],
) -> str:
    """A version built from a source, so its rows carry every system column, as a run's do."""
    from tests.integration.test_version_build import request, src
    from tests.support.stub_operators import body
    from tests.support.version_fixtures import make_source

    source = make_source(data_dir, {"train": carriers(run, ids)})
    recipe = await client.post(
        f"{API}/recipes", json={"name": run, "body": body(("fx_keep_all", {}))}
    )
    assert recipe.status_code == 201, recipe.text
    started = await request(
        client,
        dataset,
        recipe.json()["head_revision_id"],
        [src(source)],
        seed=1,
        column_roles=ROLES,
    )
    assert started.status_code == 202, started.text
    assert driver.run(started.json()["job_id"]) == "completed"
    versions = (await client.get(f"{API}/versions", params={"dataset_id": dataset})).json()["items"]
    return str(versions[0]["id"])


async def merged_build(
    client: httpx.AsyncClient,
    driver: RealDriver,
    data_dir: Path,
    first: list[str],
    second: list[str],
    recipe: dict[str, Any] = RECIPE,
) -> tuple[str, dict[str, Any]]:
    made = await client.post(f"{API}/datasets", json={"name": "merged", "target_type": "detector"})
    assert made.status_code == 201, made.text
    dataset = made.json()["id"]
    a = await built(client, driver, data_dir, dataset, "gr_full", first)
    b = await built(client, driver, data_dir, dataset, "gr_topup", second)
    assert a != b
    saved = await client.post(f"{API}/recipes", json={"name": "merge-split", "body": recipe})
    assert saved.status_code == 201, saved.text
    started = await client.post(
        f"{API}/versions",
        json={
            "dataset_id": dataset,
            "recipe_revision_id": saved.json()["head_revision_id"],
            "inputs": [
                {"kind": "version", "version_id": a},
                {"kind": "version", "version_id": b},
            ],
            "seed": 1,
        },
    )
    assert started.status_code == 202, started.text
    job_id = started.json()["job_id"]
    status = driver.run(job_id)
    return status, (await client.get(f"{API}/jobs/{job_id}")).json()


@pytest.fixture
def registered(monkeypatch: pytest.MonkeyPatch) -> None:
    reg = OperatorRegistry.build(catalogues=(), entry_points=())
    monkeypatch.setattr(operator_port, "_registry", reg)
    monkeypatch.setattr(registry_module, "_process", reg)


async def test_the_merged_build_refuses_naming_counts_and_runs(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    real_driver: RealDriver,
    registered: None,
) -> None:
    overlap = [f"same{i}" for i in range(4)]
    status, job = await merged_build(
        client,
        real_driver,
        data_dir,
        overlap + [f"full{i}" for i in range(30)],
        overlap + [f"topup{i}" for i in range(30)],
    )
    assert status == "failed"
    text = json.dumps(job)
    assert "carrier_id_collision" in text
    assert "4 carrier_id value(s)" in text and "8 rows" in text
    assert "gr_full (4)" in text and "gr_topup (4)" in text


async def test_the_same_merge_with_unique_ids_builds(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    real_driver: RealDriver,
    registered: None,
) -> None:
    status, job = await merged_build(
        client,
        real_driver,
        data_dir,
        [f"full{i}" for i in range(34)],
        [f"topup{i}" for i in range(34)],
    )
    assert status == "completed", job


def pack_merge() -> dict[str, Any]:
    """The merge as the pack writes it: the rerun's own recipe, not a copy of it."""
    pack = Path(__file__).parents[3].parent / "behaviours" / "dlp-ssn-us" / "pipeline.json"
    stages = json.loads(pack.read_text(encoding="utf-8"))["stages"]
    recipe: dict[str, Any] = next(s for s in stages if s["id"] == "merged_pass1")["recipe"]
    return recipe


async def test_the_packs_merge_refuses_a_collision_before_any_later_stage(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    real_driver: RealDriver,
    registered: None,
) -> None:
    overlap = [f"same{i}" for i in range(4)]
    status, job = await merged_build(
        client,
        real_driver,
        data_dir,
        overlap + [f"full{i}" for i in range(30)],
        overlap + [f"topup{i}" for i in range(30)],
        pack_merge(),
    )
    assert status == "failed"
    text = json.dumps(job)
    assert "carrier_id_collision" in text
    assert "4 carrier_id value(s)" in text and "8 rows" in text


async def test_the_packs_merge_builds_when_the_ids_are_unique(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    real_driver: RealDriver,
    registered: None,
) -> None:
    status, job = await merged_build(
        client,
        real_driver,
        data_dir,
        [f"full{i}" for i in range(34)],
        [f"topup{i}" for i in range(34)],
        pack_merge(),
    )
    assert status == "completed", job
