"""Code rules in real builds (feature 018 slice 4; FTASKS 13.2, 13.4 – 13.7, 13.10; FR-018.21, 018.22,
018.28, 018.30, 018.39; decisions I7, I8 = ENH-006 D36).

Real PostgreSQL, the production operator registry, the real executor and ``advance_build``; the
runner is the recording fake behind the REAL client (``fake_runner``), so every assertion reads the
payload and the call count the runner actually received. Columns are compared with values computed
from the fixture's Python twin, never with the rule set's own cases (no agreement by construction).
Both fixture behaviours.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from sqlalchemy import text

from src.core.database import get_sync_engine, sync_session_factory
from src.core.storage import resolve_under_data_dir
from src.models import Job, StepExecution, Version
from tests.integration.code_rules.conftest import approved_rule, count, revoke, saved_rule_set
from tests.integration.test_version_build import request, setup, src
from tests.support import code_rule_fixtures as fx
from tests.support.fake_runner import FakeRunner
from tests.support.version_fixtures import make_source

pytestmark = pytest.mark.usefixtures("operator_name", "production_driver")
ROLES = {"text": "content"}
#: Build rows: disjoint from the rule set's case texts, so a request carrying rows is told apart
#: from the build-load case run (FR-018.37) by its texts alone.
ROWS = ("tally 46 55 19", "SHOUT LOUDLY NOW", "plain words only", "tally", "28 91", "WE ARE HERE")


def rs_step(out: dict[str, Any], use: str = "decoy_check", prefix: str = "rs_") -> dict[str, Any]:
    return {
        "operator": "rule_set",
        "version": "1",
        "params": {
            "rule_set_name": out["name"],
            "rule_set_version": out["version"],
            "rule_set_sha256": out["sha256"],
            "use": use,
            "text_column": "text",
            "output_prefix": prefix,
        },
    }


def recipe(*steps: dict[str, Any]) -> dict[str, Any]:
    return {"format": "dw.recipe/v1", "steps": list(steps)}


def texts_for(behaviour: str) -> list[str]:
    del behaviour  # the same rows for both: each behaviour's rule fires on some, not on others
    return list(ROWS)


def row_runs(fake: FakeRunner) -> list[dict[str, Any]]:
    """The runner requests that carried build rows (never the cases' texts)."""
    return [
        r
        for r in fake.runs("stored")
        if r["lane"] == "batch" and set(r["calls"][0]["texts"]) <= set(ROWS)
    ]


async def start(
    client: httpx.AsyncClient,
    data_dir: Path,
    body: dict[str, Any],
    texts: list[str],
    name: str,
    source: str | None = None,
) -> tuple[str, str]:
    source = source or make_source(
        data_dir, {"train": [{"text": t} for t in texts]}, repo_id=f"t/{name}"
    )
    ds, rev = await setup(client, body, name=name)
    response = await request(client, ds, rev, [src(source)], seed=7, column_roles=ROLES)
    assert response.status_code == 202, response.text
    return response.json()["job_id"], source


def finished(job_id: str) -> tuple[str, dict[str, Any] | None]:
    with sync_session_factory()() as db:
        job = db.get(Job, job_id)
        assert job is not None
        return job.status, job.result


def rows_of(job_id: str) -> list[dict[str, Any]]:
    status, result = finished(job_id)
    assert status == "completed", result
    assert result is not None
    with sync_session_factory()() as db:
        version = db.get(Version, result["version_id"])
        assert version is not None
        paths = [s["path"] for s in version.splits]
    tables = [pq.read_table(resolve_under_data_dir(p)) for p in paths]
    return pa.concat_tables(tables).to_pylist() if tables else []


def rule_set_executions() -> list[StepExecution]:
    with sync_session_factory()() as db:
        rows = db.query(StepExecution).filter(StepExecution.operator_name == "rule_set").all()
        db.expunge_all()
        return sorted(rows, key=lambda r: r.created_at)


async def ready(client: httpx.AsyncClient, behaviour: str) -> tuple[dict[str, Any], dict[str, Any]]:
    rule = await approved_rule(client, behaviour)
    rs = await saved_rule_set(client, fx.rule_set_document(behaviour, rule["code_sha256"]))
    return rule, rs


# --- 13.2, 13.5: dispatch, payload, call count, conformance through the build ------------------


async def test_a_build_asks_the_runner_once_per_signal_and_chunk_with_the_documented_payload(
    client: httpx.AsyncClient,
    fake: FakeRunner,
    production_driver: Any,
    data_dir: Path,
    behaviour: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.services.code_rules import dispatch, limits

    rule, rs = await ready(client, behaviour)
    monkeypatch.setattr(dispatch, "BATCH_TEXTS", 3)
    texts = texts_for(behaviour)
    job, _ = await start(client, data_dir, recipe(rs_step(rs)), texts, f"{behaviour}-disp")
    production_driver.run(job)
    rows = rows_of(job)
    runs = row_runs(fake)
    # 6 rows, chunks of 3: two requests, one call each, every text sent once.
    assert len(runs) == 2, [r["calls"][0]["texts"] for r in fake.runs("stored")]
    assert Counter(t for r in runs for t in r["calls"][0]["texts"]) == Counter(texts)
    for r in runs:
        assert r["mode"] == "stored" and r["lane"] == "batch"
        assert r["document"]["code_sha256"] == rule["code_sha256"]
        assert r["limits"] == limits.in_force()
        (call,) = r["calls"]
        assert call["function"] == fx.RULE_SET_SHAPES[behaviour]["function"]
        assert call["params"] == fx.RULE_SET_SHAPES[behaviour]["params"]
        assert call["repeat"] == 1
    # Conformance through the build (M23): every column as the twin computes it.
    assert len(rows) == len(texts)
    for row in rows:
        want = fx.expected_columns(behaviour, row["text"])
        assert {k: row[f"rs_{k}"] for k in want} == want, row
        assert row["rs_coded__reason"] is None


async def test_the_dispatched_step_carries_the_stored_document_once_and_the_approve_row(
    client: httpx.AsyncClient,
    fake: FakeRunner,
    production_driver: Any,
    data_dir: Path,
    behaviour: str,
) -> None:
    """13.4: ``StepSpec.params`` holds the stored canonical text (exactly) and the approve row."""
    rule, rs = await ready(client, behaviour)
    job, _ = await start(
        client, data_dir, recipe(rs_step(rs)), texts_for(behaviour), f"{behaviour}-spec"
    )
    production_driver.run(job)
    assert finished(job)[0] == "completed"
    specs = [d for d in production_driver.dispatched if d["operator"] == "rule_set"]
    assert len(specs) == 1
    resolved = json.loads(specs[0]["params"]["code_rule_resolution"])
    with get_sync_engine().connect() as conn:
        canonical, version_id = conn.execute(
            text(
                "SELECT body_canonical, id FROM dw_config_versions WHERE kind = 'code_rule' "
                "AND body_sha256 = :s"
            ),
            {"s": rule["code_sha256"]},
        ).one()
        approve = conn.execute(
            text("SELECT max(id) FROM dw_code_rule_approvals WHERE action = 'approve'")
        ).scalar_one()
    assert resolved["documents"] == {rule["code_sha256"]: canonical}
    assert resolved["versions"] == {rule["code_sha256"]: version_id}
    assert resolved["approvals"] == {rule["code_sha256"]: approve}
    assert set(resolved["runner_limits"]) >= {"call_fuel", "memory_bytes", "wall_ms"}


async def test_a_raised_fuel_limit_changes_the_step_identity(
    client: httpx.AsyncClient,
    fake: FakeRunner,
    production_driver: Any,
    data_dir: Path,
    behaviour: str,
) -> None:
    from src.core import runtime_settings
    from src.core.config import get_settings

    _, rs = await ready(client, behaviour)
    body = recipe(rs_step(rs))
    job, source = await start(client, data_dir, body, texts_for(behaviour), f"{behaviour}-fuel")
    production_driver.run(job)
    assert finished(job)[0] == "completed"
    again, _ = await start(client, data_dir, body, [], f"{behaviour}-fuel2", source=source)
    production_driver.run(again)
    assert len(rule_set_executions()) == 1  # same limits: reused
    runtime_settings.apply({"code_rule_call_fuel": str(get_settings().code_rule_call_fuel * 2)})
    third, _ = await start(client, data_dir, body, [], f"{behaviour}-fuel3", source=source)
    production_driver.run(third)
    assert finished(third)[0] == "completed"
    executions = rule_set_executions()
    assert len(executions) == 2 and executions[0].identity_digest != executions[1].identity_digest


# --- 13.6: three-valued outputs and drops (FR-018.21) ------------------------------------------


async def test_one_row_exhausting_fuel_is_null_with_its_reason_and_dropped_others_unaffected(
    client: httpx.AsyncClient,
    fake: FakeRunner,
    production_driver: Any,
    data_dir: Path,
    behaviour: str,
) -> None:
    from src.core import runtime_settings

    runtime_settings.apply({"code_rule_failure_share": "0.5"})
    _, rs = await ready(client, behaviour)
    texts = texts_for(behaviour)
    bad = texts[0]
    fake.fail_texts[bad] = "rule_resource_limit"
    # use decoy_check: the row is KEPT with a null column and its reason.
    kept_job, source = await start(
        client, data_dir, recipe(rs_step(rs, use="decoy_check")), texts, f"{behaviour}-null"
    )
    production_driver.run(kept_job)
    rows = {r["text"]: r for r in rows_of(kept_job)}
    assert rows[bad]["rs_hit"] is None and rows[bad]["rs_n"] is None  # never False, never 0
    assert rows[bad]["rs_quiet"] is None
    assert rows[bad]["rs_coded__reason"] == "rule_resource_limit"
    # the pattern's own column is unaffected
    assert rows[bad]["rs_worded"] is fx.expected_columns(behaviour, bad)["worded"]
    for t in texts[1:]:
        want = fx.expected_columns(behaviour, t)
        assert {k: rows[t][f"rs_{k}"] for k in want} == want
        assert rows[t]["rs_coded__reason"] is None
    # use label: the row is DROPPED, the drop log names the signal and the reason, never the text.
    dropped_job, _ = await start(
        client,
        data_dir,
        recipe(rs_step(rs, use="label", prefix="lb_")),
        [],
        f"{behaviour}-drop",
        source=source,
    )
    production_driver.run(dropped_job)
    kept = rows_of(dropped_job)
    assert sorted(r["text"] for r in kept) == sorted(texts[1:])
    with get_sync_engine().connect() as conn:
        events = conn.execute(
            text(
                "SELECT reason_code, reason, statistic_name, statistic_text FROM dw_row_events "
                "WHERE kind = 'dropped'"
            )
        ).all()
    assert [tuple(e) for e in events] == [
        (
            "code_rule_failed",
            "coded: rule_resource_limit",
            "code_rule_failed",
            "rule_resource_limit",
        )
    ]
    assert all(bad not in str(e) for e in events)


async def test_a_build_over_the_failure_share_fails_naming_the_rule_and_leading_reason(
    client: httpx.AsyncClient,
    fake: FakeRunner,
    production_driver: Any,
    data_dir: Path,
    behaviour: str,
) -> None:
    rule, rs = await ready(client, behaviour)
    texts = texts_for(behaviour)
    fake.fail_texts[texts[0]] = "rule_error"
    job, _ = await start(client, data_dir, recipe(rs_step(rs)), texts, f"{behaviour}-share")
    production_driver.run(job)
    status, result = finished(job)
    assert status == "failed"
    assert result is not None
    error = result["error"]
    assert error["code"] == "code_rule_failure_share_exceeded", error
    assert error["details"]["code_rule"] == f"{fx.fixture(behaviour)['name']}@1"
    assert error["details"]["leading_reason"] == "rule_error"
    assert error["details"]["code_sha256"] == rule["code_sha256"]
    assert count("dw_code_rule_uses") == 0  # the failed step recorded nothing


# --- 13.7: the runner down (FR-018.22), the share (I8) and the reuse block (I7) ------------------


async def test_with_the_runner_stopped_a_mixed_build_completes_and_a_rerun_computes_afresh(
    client: httpx.AsyncClient,
    fake: FakeRunner,
    production_driver: Any,
    data_dir: Path,
    behaviour: str,
) -> None:
    _, rs = await ready(client, behaviour)
    texts = texts_for(behaviour)
    body = recipe(rs_step(rs))
    fake.down = True
    job, source = await start(client, data_dir, body, texts, f"{behaviour}-down")
    production_driver.run(job)
    rows = rows_of(job)  # completed: a host fault never fails the step (I8)
    for row in rows:
        assert row["rs_coded__reason"] == "rule_worker_unavailable"
        assert row["rs_hit"] is None and row["rs_n"] is None
        assert row["rs_worded"] == fx.expected_columns(behaviour, row["text"])["worded"]
    (first,) = rule_set_executions()
    assert first.reuse_blocked is True
    fake.down = False
    again, _ = await start(client, data_dir, body, [], f"{behaviour}-up", source=source)
    production_driver.run(again)
    executions = rule_set_executions()
    assert len(executions) == 2, "the blocked step was reused"
    assert executions[1].reuse_blocked is False
    for row in rows_of(again):
        want = fx.expected_columns(behaviour, row["text"])
        assert {k: row[f"rs_{k}"] for k in want} == want


async def test_the_switch_off_reads_every_code_row_unavailable_and_blocks_reuse(
    client: httpx.AsyncClient,
    fake: FakeRunner,
    production_driver: Any,
    data_dir: Path,
    behaviour: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FTDD §8.4: with the switch off a build still runs; every code-rule row reads
    ``rule_worker_unavailable`` and the runner is never asked."""
    from src.core.config import get_settings

    _, rs = await ready(client, behaviour)
    monkeypatch.setattr(get_settings(), "code_rules_enabled", False)
    before = len(fake.runs("stored"))
    job, _ = await start(
        client, data_dir, recipe(rs_step(rs)), texts_for(behaviour), f"{behaviour}-off"
    )
    production_driver.run(job)
    assert {r["rs_coded__reason"] for r in rows_of(job)} == {"rule_worker_unavailable"}
    assert len(fake.runs("stored")) == before
    (execution,) = rule_set_executions()
    assert execution.reuse_blocked is True
    reports = execution.reports or []
    assert reports[0]["code_rule_uses"][0]["host_fault"]["code"] == "code_rules_disabled"


async def test_a_refused_limit_is_named_in_the_report_and_never_fails_the_step_d_r2_2(
    client: httpx.AsyncClient,
    fake: FakeRunner,
    production_driver: Any,
    data_dir: Path,
    behaviour: str,
) -> None:
    _, rs = await ready(client, behaviour)
    fake.refuse_run = (422, "limit_above_ceiling", "limits.wall_ms is above RUNNER_MAX_WALL_MS")
    job, _ = await start(
        client, data_dir, recipe(rs_step(rs)), texts_for(behaviour), f"{behaviour}-ceil"
    )
    production_driver.run(job)
    assert {r["rs_coded__reason"] for r in rows_of(job)} == {"rule_worker_unavailable"}
    (execution,) = rule_set_executions()
    fault = (execution.reports or [])[0]["code_rule_uses"][0]["host_fault"]
    assert fault == {
        "code": "runner_limit_refused",
        "runner_code": "limit_above_ceiling",
        "limit": "wall_ms",
        "setting": "CODE_RULE_WALL_MS",
    }
    assert execution.reuse_blocked is True


async def test_a_starved_tail_is_sent_again_and_never_cached_d_r2_1(
    client: httpx.AsyncClient,
    fake: FakeRunner,
    production_driver: Any,
    data_dir: Path,
    behaviour: str,
) -> None:
    """The runner's lane backstop starves units past the first N of a request: the client sends
    the starved texts again until every row has its count."""
    _, rs = await ready(client, behaviour)
    fake.starve_after = 2
    texts = texts_for(behaviour)
    job, _ = await start(client, data_dir, recipe(rs_step(rs)), texts, f"{behaviour}-starve")
    production_driver.run(job)
    for row in rows_of(job):
        want = fx.expected_columns(behaviour, row["text"])
        assert {k: row[f"rs_{k}"] for k in want} == want
        assert row["rs_coded__reason"] is None
    (execution,) = rule_set_executions()
    assert execution.reuse_blocked is False


# --- 13.10: revocation between two steps; no approval for an approved hash (FR-018.28, .30) ------


async def test_a_revocation_between_two_steps_is_honoured_by_the_second(
    client: httpx.AsyncClient,
    fake: FakeRunner,
    production_driver: Any,
    data_dir: Path,
    behaviour: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rule, rs = await ready(client, behaviour)
    body = recipe(rs_step(rs, prefix="one_"), rs_step(rs, prefix="two_"))
    job, _ = await start(client, data_dir, body, texts_for(behaviour), f"{behaviour}-rev")
    from src.services import build_orchestrator

    real_ingest = build_orchestrator.ingest
    revoked: list[bool] = []

    def ingest_then_revoke(*args: Any, **kwargs: Any) -> Any:
        out = real_ingest(*args, **kwargs)
        if not revoked and args[1].operator_name == "rule_set":
            # The first step is complete; the second is not resolved yet.
            with get_sync_engine().begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO dw_code_rule_approvals (code_sha256, action, reason, "
                        "changed_by, origin) VALUES (:s, 'revoke', 'between steps', 'Ada', "
                        "'operator')"
                    ),
                    {"s": rule["code_sha256"]},
                )
            revoked.append(True)
        return out

    monkeypatch.setattr(build_orchestrator, "ingest", ingest_then_revoke)
    production_driver.run(job)
    status, result = finished(job)
    assert status == "failed" and result is not None
    assert result["error"]["code"] == "code_rule_not_approved", result["error"]
    assert result["error"]["details"]["state"] == "revoked"
    executions = rule_set_executions()
    assert [e.state for e in executions] == ["completed"]  # the second never dispatched


async def test_a_build_after_a_revocation_refuses_at_load_naming_the_code_rule(
    client: httpx.AsyncClient,
    fake: FakeRunner,
    production_driver: Any,
    data_dir: Path,
    behaviour: str,
) -> None:
    rule, rs = await ready(client, behaviour)
    await revoke(client, rule["code_sha256"])
    job, _ = await start(
        client, data_dir, recipe(rs_step(rs)), texts_for(behaviour), f"{behaviour}-load"
    )
    production_driver.run(job)
    status, result = finished(job)
    assert status == "failed" and result is not None
    assert result["error"]["code"] == "code_rule_not_approved"
    assert result["error"]["details"]["code_sha256"] == rule["code_sha256"]
    assert not [d for d in production_driver.dispatched if d["operator"] == "rule_set"]


async def test_an_approved_hash_runs_in_a_build_without_any_new_approval(
    client: httpx.AsyncClient,
    fake: FakeRunner,
    production_driver: Any,
    data_dir: Path,
    behaviour: str,
) -> None:
    _, rs = await ready(client, behaviour)
    approvals_before = count("dw_code_rule_approvals")
    job, _ = await start(
        client, data_dir, recipe(rs_step(rs)), texts_for(behaviour), f"{behaviour}-noappr"
    )
    production_driver.run(job)
    assert finished(job)[0] == "completed"
    assert count("dw_approvals") == 0
    assert count("dw_code_rule_approvals") == approvals_before
