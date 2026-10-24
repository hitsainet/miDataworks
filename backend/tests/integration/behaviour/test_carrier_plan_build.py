"""Carrier plans through real builds (017 FTASKS 3.6, 4.2, 4.4 (recorded configuration), 4.5, 4.6;
FR-017.11, FR-017.13, FR-017.17, FR-017.55).

The production operator registry (plus the test-only ``fx_keep_all``), the real orchestrator and
the real finalize, for BOTH fixture behaviours. What the build records is read back through the
REST route and through the MCP tools over the same backend (``tests/support/mcp_live.py``).
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
import pyarrow.parquet as pq
import pytest
from sqlalchemy import select

from src.core.database import sync_session_factory
from src.models import Job
from src.models.version import Version
from src.operators.native.behaviour.carrier_plan import configuration_of
from src.services.generation import rules
from tests.integration.test_version_build import request, setup, src
from tests.support.behaviour_fixtures import BEHAVIOURS, INDEX_ROLES, definition, plan_params
from tests.support.mcp_live import call, live_mcp
from tests.support.operator_build_driver import RealDriver
from tests.support.version_fixtures import make_source

V = "/api/v1/versions"
KEEP = {"operator": "fx_keep_all", "version": "1", "params": {}}


def plan_step(key: str, rows: int, **over: Any) -> dict[str, Any]:
    return {
        "operator": "carrier_plan",
        "version": "1.0.0",
        "params": plan_params(key, rows, **over),
    }


async def build(
    client: httpx.AsyncClient,
    driver: RealDriver,
    steps: list[dict[str, Any]],
    inputs: list[dict[str, Any]],
    name: str,
) -> dict[str, Any]:
    ds, rev = await setup(
        client, {"format": "dw.recipe/v1", "steps": steps}, name=name, target="detector"
    )
    response = await request(client, ds, rev, inputs, seed=7, column_roles=INDEX_ROLES)
    assert response.status_code == 202, response.text
    status = driver.run(response.json()["job_id"])
    with sync_session_factory()() as db:
        job = db.get(Job, response.json()["job_id"])
        assert job is not None and job.result is not None
        assert status == "completed", (job.error, job.result)
        version_id = job.result["version_id"]
    got = await client.get(f"{V}/{version_id}")
    assert got.status_code == 200, got.text
    return dict(got.json())


def index_source(data_dir: Path, rows: int, name: str) -> str:
    return make_source(
        data_dir, {"train": [{"idx": i} for i in range(rows)]}, repo_id=f"t/{name}",
        text_columns=("idx",),
    )  # fmt: skip


async def plan_version(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, key: str, rows: int, **over: Any
) -> dict[str, Any]:
    source = index_source(data_dir, rows, f"{key}-{rows}")
    return await build(client, driver, [plan_step(key, rows, **over)], [src(source)], f"{key}-plan")


@pytest.mark.parametrize("key", BEHAVIOURS)
async def test_a_plan_version_records_what_it_was_built_against(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str, key: str
) -> None:
    rows = 240
    version = await plan_version(client, driver, data_dir, key, rows)
    # 4.5: the declared held-out split is written empty and counted as held out
    by = {s["name"]: s for s in version["splits"]}
    assert by["test"]["rows"] == 0 and by["test"]["held_out"] is True, by
    assert by["test"]["declared_empty"] is True
    assert pq.read_table(data_dir / by["test"]["path"]).num_rows == 0
    assert by["train"]["rows"] == rows and by["train"]["held_out"] is False
    assert version["held_out_origin_version_id"] == version["id"]
    held = rules.held_out_guard(version["splits"], version["held_out_origin_version_id"])
    assert held == ["test"]
    rules.seed_split_guard(["train"], held)  # the plan rows are seeds
    # 3.6 / FR-017.55: the definition and the configuration supplied are what the build records
    params = plan_params(key, rows)
    _, expected = configuration_of(params)
    assert version["behaviour_definition"] == definition(key)
    assert version["behaviour_configuration"] == json.loads(json.dumps(expected))
    census = version["behaviour_census"]
    assert census["rows"] == rows and census["cells_short"] == 0
    # the same, read back through the MCP tool over the same backend
    mcp, mcp_client = live_mcp()
    try:
        via = await call(mcp, "dataworks_get_version", {"version_id": version["id"]})
    finally:
        await mcp_client.close()
    assert via["behaviour_definition"] == version["behaviour_definition"]
    assert via["behaviour_configuration"] == version["behaviour_configuration"]
    assert {s["name"]: s["rows"] for s in via["splits"]} == {"train": rows, "test": 0}


async def test_the_two_fixtures_build_different_configurations_with_the_same_code(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    alpha = await plan_version(client, driver, data_dir, "fixture-alpha", 120)
    beta = await plan_version(client, driver, data_dir, "fixture-beta", 120)
    a, b = alpha["behaviour_configuration"], beta["behaviour_configuration"]
    assert a["behaviour_key"] == "fixture-alpha" and b["behaviour_key"] == "fixture-beta"
    assert set(a["vocabulary"]["genres"]).isdisjoint(b["vocabulary"]["genres"])
    assert a["vocabulary"]["placements"] != b["vocabulary"]["placements"]
    files = {
        k: pq.read_table(data_dir / next(s for s in v["splits"] if s["name"] == "train")["path"])
        for k, v in (("a", alpha), ("b", beta))
    }
    assert set(files["b"].column("placement").to_pylist()) == {"whole"}
    assert set(files["b"].column("slot").to_pylist()) == {None}
    assert "planned_complied" in files["b"].schema.names
    assert "planned_element_valid" in files["a"].schema.names


async def test_a_child_inherits_the_records_and_the_empty_split(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    parent = await plan_version(client, driver, data_dir, "fixture-alpha", 90)
    child = await build(
        client, driver, [KEEP], [{"kind": "version", "version_id": parent["id"]}], "plan-child"
    )
    assert child["behaviour_definition"] == parent["behaviour_definition"]
    assert child["behaviour_configuration"] == parent["behaviour_configuration"]
    by = {s["name"]: s for s in child["splits"]}
    assert by["test"]["rows"] == 0 and by["test"]["held_out"] and by["test"]["declared_empty"]
    assert child["held_out_origin_version_id"] == parent["id"]


async def test_the_census_flags_a_short_framing_and_the_build_proceeds(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    """FR-017.17: a version that lost rows of one framing in one genre is flagged with the
    shortfall, not refused."""
    parent = await plan_version(client, driver, data_dir, "fixture-beta", 400)
    train = next(s for s in parent["splits"] if s["name"] == "train")
    rows = pq.read_table(data_dir / train["path"]).to_pylist()
    lost = [r["carrier_id"] for r in rows if r["genre"] == "memo" and r["framing"] == "refuses"]
    assert lost
    drop = {
        "operator": "metadata_value_filter",
        "version": "1.0.0",
        "params": {"column": "carrier_id", "values": lost},
    }
    child = await build(
        client, driver, [drop], [{"kind": "version", "version_id": parent["id"]}], "short"
    )
    census = child["behaviour_census"]
    assert census["rows"] == 400 - len(lost)
    codes = [f["code"] for f in census["flags"]]
    assert "framing_short" in codes and "cell_short" in codes
    (worst,) = [f for f in census["framing_shortfalls"] if f["framing"] == "refuses"]
    assert worst == {"genre": "memo", "framing": "refuses", "shortfall": len(lost)}
    assert census["framing_share_by_genre"]["memo"]["refuses"]["rows"] == 0
    shortest = census["cells"][0]
    assert shortest["framing"] == "refuses" and shortest["genre"] == "memo" and shortest["short"]
    assert any(w["code"] == "behaviour_census_flags" for w in child["warnings"])
    with sync_session_factory()() as db:
        stored = db.get(Version, child["id"])
        assert stored is not None and stored.behaviour_census == census
    assert Counter(c["short"] for c in census["cells"])[True] == census["cells_short"]


REPORT_INPUT = {
    "operator": "fx_report_params",
    "version": "1",
    "params": {"vocabulary": "@input", "definition": "@input"},
}


def ran_with(version: dict[str, Any]) -> dict[str, Any]:
    (step,) = [s for s in version["step_reports"] if s["operator"] == "fx_report_params"]
    (report,) = step["reports"]
    return dict(report["ran_with"])


def step_identity(version_id: str, operator: str) -> str:
    from src.models.step_execution import StepExecution
    from src.models.version import VersionStep

    with sync_session_factory()() as db:
        rows = db.execute(
            select(StepExecution.identity_digest, StepExecution.params_hash)
            .join(VersionStep, VersionStep.step_execution_id == StepExecution.id)
            .where(VersionStep.version_id == version_id)
            .where(StepExecution.operator_name == operator)
        ).all()
    ((identity, params_hash),) = rows
    return f"{identity}:{params_hash}"


async def test_an_input_reference_reads_the_recorded_configuration(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    """FTID §7.10 (TD-017.1): a later step's ``"@input"`` parameter runs with the literal
    configuration its input version recorded; another input is another step identity."""
    validated = await client.post(
        "/api/v1/recipes/validate",
        json={"body": {"format": "dw.recipe/v1", "steps": [REPORT_INPUT]}},
    )
    assert validated.status_code == 200 and validated.json()["valid"], validated.text
    seen: dict[str, dict[str, Any]] = {}
    identities = {}
    for key in BEHAVIOURS:
        plan = await plan_version(client, driver, data_dir, key, 60)
        child = await build(
            client,
            driver,
            [REPORT_INPUT],
            [{"kind": "version", "version_id": plan["id"]}],
            f"{key}-reads-input",
        )
        seen[key] = ran_with(child)
        assert json.loads(seen[key]["vocabulary"]) == plan["behaviour_configuration"]["vocabulary"]
        assert json.loads(seen[key]["definition"]) == plan["behaviour_definition"]
        identities[key] = step_identity(child["id"], "fx_report_params")
    assert seen["fixture-alpha"] != seen["fixture-beta"]
    assert identities["fixture-alpha"] != identities["fixture-beta"]


async def test_a_literal_parameter_is_never_replaced_by_the_input_record(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    """Review R2: only a parameter whose value IS ``"@input"`` reads the record; a registered
    parameter given its own value runs with that value over a plan version, and an omitted one
    stays omitted."""
    plan = await plan_version(client, driver, data_dir, "fixture-alpha", 30)
    literal = json.dumps({"genres": ["given-here"]})
    step = {"operator": "fx_report_params", "version": "1", "params": {"vocabulary": literal}}
    child = await build(
        client, driver, [step], [{"kind": "version", "version_id": plan["id"]}], "literal-params"
    )
    assert ran_with(child) == {"vocabulary": literal}


async def test_an_input_reference_over_an_input_that_recorded_nothing_is_refused(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    source = index_source(data_dir, 10, "no-records")
    ds, rev = await setup(
        client, {"format": "dw.recipe/v1", "steps": [REPORT_INPUT]}, name="nothing-recorded"
    )
    response = await request(client, ds, rev, [src(source)], seed=7, column_roles=INDEX_ROLES)
    assert response.status_code == 202, response.text
    status = driver.run(response.json()["job_id"])
    with sync_session_factory()() as db:
        job = db.get(Job, response.json()["job_id"])
        assert job is not None
        assert status == "failed", job.result
        error = (job.result or {})["error"]
    assert error["code"] == "input_not_recorded", error
    assert error["details"]["param"] == "definition"
    assert error["details"]["next"]["tool"] == "dataworks_get_version"
