"""Value-directed held-out splits through real builds (017 FTASKS 2.5, 2.6, 2.7; FR-017.1 - FR-017.3).

The production operator registry plus the test-only ``fx_generator_echo`` (a generator that echoes
each row as a ``generated`` row), the real orchestrator and the real finalize. Rows cross genre and
generator; each carrier's two rows share its genre and generator.

- a directed split of generated rows with a passing proof builds, the proof is stored on the
  version's ``splits`` entry and returned by ``GET /versions/{id}``;
- recomputing the proof from the written split files gives the stored record;
- a child version keeps ``directed_by`` and proves it again over ITS files; a child that adds rows
  carrying a held-out value to training is refused ``held_out_not_disjoint``, naming value and count;
- a random (not directed) held-out split still refuses generated rows.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pyarrow.parquet as pq
import pytest

from src.core.database import sync_session_factory
from src.models import Job
from src.operators import executor
from src.operators import registry as registry_module
from src.operators.native.fixtures import FIXTURE_OPERATORS
from src.operators.native.registrations import PRODUCT_OPERATORS
from src.operators.registry import OperatorRegistry
from src.services import operator_port
from src.services.curation.held_out_files import prove_files
from src.services.curation.held_out_proof import CITATION, DirectedBy
from tests.integration.test_version_build import request, setup, src
from tests.support.operator_build_driver import RealDriver
from tests.support.version_fixtures import make_source

V = "/api/v1/versions"
ROLES = {"text": "content"}
GENRES = ("g1", "g2", "g3", "g4")
MODELS = ("gen-a", "gen-b@r1", "gen-c@r1")
SPLIT: dict[str, Any] = {
    "operator": "split",
    "version": "1.1.0",
    "params": {
        "split_names": ["train", "test", "ood_genre", "ood_generator", "ood_both"],
        "split_fractions": [0.8, 0.2, 0.0, 0.0, 0.0],
        "held_out": ["ood_genre", "ood_generator", "ood_both"],
        "group_columns": ["carrier_id"],
        "directing_columns": ["genre", "generator"],
        "held_out_values": ["g4", "gen-c"],
        "held_out_value_columns": ["genre", "generator"],
        "identity_columns": ["generator"],
        "held_out_split_names": ["ood_genre", "ood_generator", "ood_both"],
    },
}
ECHO = {"operator": "fx_generator_echo", "version": "1", "params": {}}
KEEP = {"operator": "fx_keep_all", "version": "1", "params": {}}


@pytest.fixture
def driver(monkeypatch: pytest.MonkeyPatch, data_dir: Path) -> Iterator[RealDriver]:
    """``RealDriver`` over the production natives plus the test-only fixture operators."""
    from src.core.celery_app import celery_app
    from src.workers import version_build_tasks

    reg = OperatorRegistry.build(
        native=(*PRODUCT_OPERATORS, *FIXTURE_OPERATORS), catalogues=(), entry_points=()
    )
    monkeypatch.setattr(operator_port, "_registry", reg)
    monkeypatch.setattr(registry_module, "_process", reg)
    drv = RealDriver(reg)
    monkeypatch.setattr(executor, "send_task", drv.record_step)
    monkeypatch.setattr(celery_app, "send_task", drv.send_task)
    monkeypatch.setattr(version_build_tasks, "_send_task", drv.send_task)
    monkeypatch.setattr(version_build_tasks, "emit", drv.emit)
    yield drv


def carriers(tag: str, genres: tuple[str, ...] = GENRES, n: int = 3) -> list[dict[str, Any]]:
    return [
        {
            "text": f"{tag} {genre} {model} carrier {c}",
            "genre": genre,
            "generator": model,
            "carrier_id": f"{tag}-{genre}-{model}-{c}",
        }
        for genre in genres
        for model in MODELS
        for c in range(n)
    ]


async def run(
    client: httpx.AsyncClient,
    driver: RealDriver,
    steps: list[dict[str, Any]],
    inputs: list[dict[str, Any]],
    name: str,
    target: str = "detector",
) -> dict[str, Any]:
    """The build's job: ``{"status", "version_id"?, "error"?}``."""
    ds, rev = await setup(
        client, {"format": "dw.recipe/v1", "steps": steps}, name=name, target=target
    )
    response = await request(client, ds, rev, inputs, seed=7, column_roles=ROLES)
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    status = driver.run(job_id)
    with sync_session_factory()() as db:
        job = db.get(Job, job_id)
        assert job is not None and job.result is not None
        return {"status": status, **job.result}


async def directed_version(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, name: str = "directed"
) -> dict[str, Any]:
    source = make_source(data_dir, {"train": carriers("s")}, repo_id=f"t/{name}")
    out = await run(client, driver, [ECHO, SPLIT], [src(source)], name)
    assert out["status"] == "completed", out
    got = await client.get(f"{V}/{out['version_id']}")
    assert got.status_code == 200, got.text
    version: dict[str, Any] = got.json()
    return version


def values(data_dir: Path, split: dict[str, Any], column: str) -> set[str]:
    return set(pq.read_table(data_dir / split["path"], columns=[column]).column(0).to_pylist())


async def test_a_directed_split_of_generated_rows_builds_and_stores_its_proof(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    version = await directed_version(client, driver, data_dir)
    by = {s["name"]: s for s in version["splits"]}
    assert set(by) == {"train", "test", "ood_genre", "ood_generator", "ood_both"}
    for name in ("train", "test"):
        assert "g4" not in values(data_dir, by[name], "genre")
        assert "gen-c@r1" not in values(data_dir, by[name], "generator")
        assert by[name].get("proof") is None and by[name].get("directed_by") is None
    for name, columns in (
        ("ood_genre", ["genre"]),
        ("ood_generator", ["generator"]),
        ("ood_both", ["genre", "generator"]),
    ):
        split = by[name]
        assert split["held_out"] and split["directed_by"]["columns"] == columns
        proof = split["proof"]
        assert proof["passes"] and proof["columns"] == columns and proof["rule"] == CITATION
        assert proof["generated_rows"] == split["rows"] // 2 > 0, "echoes sit beside sources"
        assert proof["intersection"] == [] and proof["shared_groups"] == {"carrier_id": 0}
        assert proof["training_splits"] == ["train", "test"]
    assert by["ood_genre"]["proof"]["training_values"] == {"genre": 3}
    assert by["ood_generator"]["proof"]["training_values"] == {"generator": 2}
    # re-checkable: the proof recomputed from the written files equals the stored record
    for name in ("ood_genre", "ood_generator", "ood_both"):
        again = prove_files(
            name,
            DirectedBy.from_dict(by[name]["directed_by"]),
            [data_dir / by[name]["path"]],
            {t: data_dir / by[t]["path"] for t in ("train", "test")},
        )
        assert again.as_dict() == by[name]["proof"]


async def test_a_child_keeps_the_direction_and_a_leaking_child_is_refused(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    parent = await directed_version(client, driver, data_dir, "parent")
    child = await run(
        client, driver, [KEEP], [{"kind": "version", "version_id": parent["id"]}], "child"
    )
    assert child["status"] == "completed", child
    got = (await client.get(f"{V}/{child['version_id']}")).json()
    kept = {s["name"]: s for s in got["splits"]}
    assert (
        kept["ood_genre"]["directed_by"]
        == {s["name"]: s for s in parent["splits"]}["ood_genre"]["directed_by"]
    )
    assert kept["ood_genre"]["proof"]["passes"], "its own proof, over its own files"
    assert kept["ood_genre"]["path"].startswith(f"versions/{child['version_id']}/")
    # a child that brings new g4 rows into training (a source read into "train") is refused
    extra = make_source(data_dir, {"train": carriers("x", genres=("g4",), n=1)}, repo_id="t/leak")
    leaking = await run(
        client,
        driver,
        [KEEP],
        [{"kind": "version", "version_id": parent["id"]}, src(extra)],
        "leaking-child",
    )
    assert leaking["status"] == "failed", leaking
    error = leaking["error"]
    assert error["code"] == "held_out_not_disjoint", error
    assert error["details"]["value"] == "g4" and error["details"]["count"] == 3  # one per model
    assert CITATION in error["message"]
    from tests.integration.rules.conftest import live_tools

    assert error["details"]["next"]["tool"] in await live_tools()


async def test_a_random_held_out_split_still_refuses_generated_rows(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    source = make_source(data_dir, {"train": carriers("r")}, repo_id="t/random")
    plain = {
        "operator": "split",
        "version": "1.1.0",
        "params": {
            "split_names": ["train", "test"],
            "split_fractions": [0.8, 0.2],
            "held_out": ["test"],
            "group_columns": ["carrier_id"],
        },
    }
    out = await run(client, driver, [ECHO, plain], [src(source)], "random")
    assert out["status"] == "failed" and out["error"]["code"] == "generated_in_held_out", out


async def test_the_proof_and_the_split_parameters_reach_through_mcp(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    """FR-017.1 / FR-017.2 reach: the registered tools carry the split parameters unchanged and
    return the stored proof (every module of the registry, one recording client)."""
    from tests.support.mcp_harness import build_harness, tool_context

    version = await directed_version(client, driver, data_dir, "through-mcp")
    mcp, recorder = build_harness(response=version)
    got = await mcp._tool_manager.call_tool(  # noqa: SLF001
        "dataworks_get_version", {"version_id": version["id"]}, tool_context(mcp)
    )
    assert recorder.calls == [("GET", f"/versions/{version['id']}", {})]
    proofs = {s["name"]: s.get("proof") for s in got["splits"]}
    assert proofs["ood_genre"]["passes"] and proofs["ood_genre"]["held_out_values"] == {
        "genre": ["g4"]
    }
    body = {"format": "dw.recipe/v1", "steps": [ECHO, SPLIT]}
    mcp, recorder = build_harness()
    await mcp._tool_manager.call_tool(  # noqa: SLF001
        "dataworks_validate_recipe", {"body": body}, tool_context(mcp)
    )
    ((method, path, kwargs),) = recorder.calls
    assert (method, path) == ("POST", "/recipes/validate")
    assert kwargs["json_body"] == {"body": body}, "the split parameters travel unchanged"
    checked = await client.post("/api/v1/recipes/validate", json=kwargs["json_body"])
    assert checked.status_code == 200 and checked.json()["valid"], checked.text
    broken = {**SPLIT, "params": {**SPLIT["params"], "split_fractions": [0.7, 0.2, 0.1, 0, 0]}}
    refused = await client.post(
        "/api/v1/recipes/validate",
        json={"body": {"format": "dw.recipe/v1", "steps": [ECHO, broken]}},
    )
    assert refused.status_code == 200 and not refused.json()["valid"], refused.text
    assert "directed_split_name_conflict" in refused.text
    mcp, recorder = build_harness()
    build_args = {
        "dataset_id": version["dataset_id"],
        "recipe_revision_id": version["recipe_revision_id"],
        "inputs": version["inputs"],
        "seed": 7,
    }
    await mcp._tool_manager.call_tool(  # noqa: SLF001
        "dataworks_build_version", build_args, tool_context(mcp)
    )
    ((method, path, kwargs),) = recorder.calls
    assert (method, path) == ("POST", "/versions")
    assert kwargs["json_body"]["recipe_revision_id"] == version["recipe_revision_id"]


@pytest.mark.parametrize("dropped", [["genre"], ["genre", "generator"]])
async def test_a_step_dropping_the_directing_column_after_the_split_is_refused(
    client: httpx.AsyncClient,
    driver: RealDriver,
    data_dir: Path,
    operator_name: str,
    dropped: list[str],
) -> None:
    """Review R1: the split step proves its own input; a later step that drops ``genre`` leaves
    the written held-out files with no directing value. Finalize's proof over the WRITTEN files
    must refuse, not pass over zero counted rows (the junction of the step and finalize). Dropping
    every directing column is the case where a file has none of them at all."""
    tag = "-".join(dropped)
    source = make_source(data_dir, {"train": carriers("d")}, repo_id=f"t/dropped-{tag}")
    drop = {"operator": "drop_columns", "version": "1", "params": {"columns": dropped}}
    out = await run(client, driver, [ECHO, SPLIT, drop], [src(source)], f"dropped-{tag}")
    assert out["status"] == "failed", out
    assert out["error"]["code"] in ("held_out_value_null", "held_out_not_disjoint"), out["error"]
    assert CITATION in out["error"]["message"]
