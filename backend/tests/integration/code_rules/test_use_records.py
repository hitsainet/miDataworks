"""Use records: what a build step's code rules did (feature 018 slice 4; FTASKS 13.8; FR-018.39,
FR-018.33; FTDD §5.6, §7.7; D-R2-5).

A fixture build records one ``dw_code_rule_uses`` row per code rule (the payload asserted: the
step, the hash, the rule set, the exact version, the runner's contract and bundle, the approve row,
the failed rows); the code rule's ``used_by`` lists the version; a purge of the archived fixture
dataset removes the use rows with its executions; an archived code rule purges only when unused, and
a used one is refused naming the version that ran it (a NAMED refusal now, not the request schema's
generic 422 - Review R2 D-R2-5).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from src.core.database import get_sync_engine
from tests.integration.code_rules.conftest import CR, approved_rule, count, saved_rule_set
from tests.integration.code_rules.test_build_dispatch import (
    finished,
    recipe,
    rs_step,
    start,
    texts_for,
)
from tests.support import code_rule_fixtures as fx
from tests.support.fake_runner import FakeRunner

pytestmark = pytest.mark.usefixtures("operator_name", "production_driver")
P = "/api/v1/purge"


def _uses() -> list[dict[str, Any]]:
    with get_sync_engine().connect() as conn:
        rows = conn.execute(
            text(
                "SELECT u.step_execution_id::text, u.code_sha256, rs.name, rs.number, cr.name, "
                "cr.number, u.runner_contract, u.contract_version, u.bundle_digest, "
                "u.approval_row_id, u.failed_rows, u.reasons FROM dw_code_rule_uses u "
                "JOIN dw_config_versions rs ON rs.id = u.rule_set_config_version_id "
                "JOIN dw_config_versions cr ON cr.id = u.code_rule_config_version_id"
            )
        ).all()
    keys = (
        "execution",
        "code_sha256",
        "rule_set",
        "rule_set_version",
        "code_rule",
        "code_rule_version",
        "runner_contract",
        "contract_version",
        "bundle_digest",
        "approval_row_id",
        "failed_rows",
        "reasons",
    )
    return [dict(zip(keys, r, strict=True)) for r in rows]


async def _built(
    client: httpx.AsyncClient, driver: Any, data_dir: Path, behaviour: str, name: str
) -> tuple[dict[str, Any], dict[str, Any], str]:
    rule = await approved_rule(client, behaviour)
    rs = await saved_rule_set(client, fx.rule_set_document(behaviour, rule["code_sha256"]))
    job, _ = await start(client, data_dir, recipe(rs_step(rs)), texts_for(behaviour), name)
    driver.run(job)
    status, result = finished(job)
    assert status == "completed" and result is not None, result
    return rule, rs, str(result["version_id"])


async def test_a_build_records_each_code_rule_it_ran_with_the_approve_row_m22(
    client: httpx.AsyncClient,
    fake: FakeRunner,
    production_driver: Any,
    data_dir: Path,
    behaviour: str,
) -> None:
    rule, rs, _ = await _built(client, production_driver, data_dir, behaviour, f"{behaviour}-use")
    with get_sync_engine().connect() as conn:
        approve = conn.execute(
            text("SELECT id FROM dw_code_rule_approvals WHERE code_sha256 = :s"),
            {"s": rule["code_sha256"]},
        ).scalar_one()
        execution = conn.execute(
            text("SELECT id::text FROM dw_step_executions WHERE operator_name = 'rule_set'")
        ).scalar_one()
    assert _uses() == [
        {
            "execution": execution,
            "code_sha256": rule["code_sha256"],
            "rule_set": rs["name"],
            "rule_set_version": 1,
            "code_rule": fx.fixture(behaviour)["name"],
            "code_rule_version": 1,
            "runner_contract": "midataworks.runner/v1",
            "contract_version": fake.contract_version,
            "bundle_digest": fake.bundle_digest,
            "approval_row_id": approve,
            "failed_rows": 0,
            "reasons": {},
        }
    ]


async def test_the_code_rule_lists_the_version_that_ran_it(
    client: httpx.AsyncClient,
    fake: FakeRunner,
    production_driver: Any,
    data_dir: Path,
    behaviour: str,
) -> None:
    _, rs, version_id = await _built(
        client, production_driver, data_dir, behaviour, f"{behaviour}-usedby"
    )
    name = fx.fixture(behaviour)["name"]
    got = (await client.get(f"{CR}/{name}/versions/1")).json()
    assert got["used_by"]["recorded"] is True
    (used,) = got["used_by"]["versions"]
    assert used["version_id"] == version_id and used["rule_set"] == f"{rs['name']}@1"
    assert used["failed_rows"] == 0


async def test_failed_rows_and_reasons_are_recorded(
    client: httpx.AsyncClient,
    fake: FakeRunner,
    production_driver: Any,
    data_dir: Path,
    behaviour: str,
) -> None:
    from src.core import runtime_settings

    runtime_settings.apply({"code_rule_failure_share": "0.9"})
    fake.fail_texts[texts_for(behaviour)[1]] = "rule_output_invalid"
    await _built(client, production_driver, data_dir, behaviour, f"{behaviour}-failrec")
    (use,) = _uses()
    assert use["failed_rows"] == 1 and use["reasons"] == {"rule_output_invalid": 1}


async def test_a_purge_of_the_archived_dataset_removes_its_use_rows(
    client: httpx.AsyncClient,
    fake: FakeRunner,
    production_driver: Any,
    data_dir: Path,
    behaviour: str,
) -> None:
    _, _, version_id = await _built(
        client, production_driver, data_dir, behaviour, f"{behaviour}-purge"
    )
    with get_sync_engine().connect() as conn:
        dataset_id = conn.execute(
            text("SELECT dataset_id::text FROM dw_versions WHERE id = CAST(:v AS uuid)"),
            {"v": version_id},
        ).scalar_one()
    assert (await client.post(f"/api/v1/datasets/{dataset_id}/archive")).status_code == 200
    purged = await client.post(f"{P}/dataset/{dataset_id}", json={"reason": "test"})
    assert purged.status_code == 200, purged.text
    assert purged.json()["counts"]["code_rule_uses"] == 1
    assert count("dw_code_rule_uses") == 0
    # The code rule, its versions and the operator's decisions stay.
    assert count("dw_config_versions", "kind = 'code_rule'") == 1
    assert count("dw_code_rule_approvals") == 1


async def test_a_used_code_rule_purge_is_refused_naming_the_version_and_an_unused_one_purges(
    client: httpx.AsyncClient,
    fake: FakeRunner,
    production_driver: Any,
    data_dir: Path,
) -> None:
    await _built(client, production_driver, data_dir, "fixture-gamma", "used-rule")
    used = fx.fixture("fixture-gamma")["name"]
    assert (await client.post(f"{CR}/{used}/archive")).status_code == 200
    refused = await client.post(f"{P}/code_rule/{used}")
    assert refused.status_code == 409, refused.text
    error = refused.json()["error"]
    assert error["code"] == "purge_blocked"
    tables = {b["table"] for b in error["details"]["blockers"]}
    assert {"dw_code_rule_uses", "dw_config_versions"} <= tables, error
    # An unused, archived code rule purges: versions and head go; its decisions stay.
    await approved_rule(client, "fixture-delta")
    unused = fx.fixture("fixture-delta")["name"]
    assert (await client.post(f"{CR}/{unused}/archive")).status_code == 200
    out = await client.post(f"{P}/code_rule/{unused}", json={"reason": "test"})
    assert out.status_code == 200, out.text
    assert out.json()["counts"] == {"code_rule_versions": 1, "code_rules": 1}
    assert count("dw_code_rules", "name = :n", n=unused) == 0
    assert count("dw_code_rule_approvals") == 2
    assert count("dw_purge_events", "entity_kind = 'code_rule'") == 1
