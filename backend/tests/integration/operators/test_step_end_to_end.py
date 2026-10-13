"""A native step end to end through feature 002's build job and callback (FTASKS 6.5–6.9, 12.3).

Real PostgreSQL, the real registry, the real executor and the real ``advance_build``; only the
broker is replaced by ``RealDriver``, which follows the ``link`` the executor actually sent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pyarrow.parquet as pq
import pytest
from sqlalchemy import text

from src.core import cancellation
from src.core.config import get_settings
from src.core.database import get_sync_engine, sync_session_factory
from src.models.step_execution import StepExecution
from src.operators import executor
from src.services.row_keys import compute_row_key
from src.services.step_contract import part_files
from tests.integration.test_version_build import request, setup, src
from tests.support.operator_build_driver import RealDriver, real_driver
from tests.support.stub_operators import body
from tests.support.version_fixtures import HUMOR_TRAIN, make_source

__all__ = ["real_driver"]
V = "/api/v1/versions"


def _key(text_value: str) -> str:
    return compute_row_key({"text": text_value}, ["text"])


async def _start(
    client: httpx.AsyncClient, data_dir: Path, *steps: tuple[str, dict[str, Any]]
) -> str:
    source = make_source(data_dir, {"train": HUMOR_TRAIN})
    ds, rev = await setup(client, body(*steps))
    response = await request(client, ds, rev, [src(source)], seed=1)
    assert response.status_code == 202, response.text
    job_id: str = response.json()["job_id"]
    return job_id


def _events(job_id: str) -> list[dict[str, Any]]:
    with get_sync_engine().connect() as conn:
        rows = conn.execute(
            text(
                "SELECT e.kind, e.reason_code, e.statistic_name, e.statistic_value "
                "FROM dw_row_events e JOIN dw_step_executions s ON s.id = e.step_execution_id "
                "WHERE s.job_id = :job"
            ),
            {"job": job_id},
        ).mappings()
        return [dict(r) for r in rows]


def _result(job_id: str) -> dict[str, Any]:
    with get_sync_engine().connect() as conn:
        row = conn.execute(
            text("SELECT status, result FROM dw_jobs WHERE id = :id"), {"id": job_id}
        ).one()
    return {"status": row[0], "result": row[1] or {}}


async def test_native_step_builds_a_version_and_explains_its_drops(
    client: httpx.AsyncClient, real_driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    """6.8: events stored; "why did this row leave?" answers for a dropped row (FR-002.26)."""
    job_id = await _start(client, data_dir, ("fx_drop_short", {"min_len": 20}))
    assert real_driver.run(job_id) == "completed"
    assert real_driver.step_results == [
        {
            "status": "completed",
            "step_execution_id": real_driver.step_results[0]["step_execution_id"],
            "rows_in": 10,
            "rows_dropped": 1,
            "pinned": None,
        }
    ]
    events = _events(job_id)
    assert events == [
        {
            "kind": "dropped",
            "reason_code": "too_short",
            "statistic_name": "text_length",
            "statistic_value": 5.0,
        }
    ]
    version_id = _result(job_id)["result"]["version_id"]
    answer = await client.get(f"{V}/{version_id}/rows/history", params={"row_key": _key("short")})
    assert answer.status_code == 200, answer.text
    dropped = answer.json()["results"][0]["dropped_at"]
    assert dropped["operator"] == "fx_drop_short" and dropped["reason_code"] == "too_short"
    assert dropped["statistic_value"] == 5.0
    assert dropped["threshold"] == {"value": 20, "comparator": "<"}


@pytest.mark.parametrize(
    ("operator", "code"),
    [
        ("fx_lose_row", "conservation_violated"),
        ("fx_drop_no_reason", "event_invalid"),
        ("fx_filter_changes", "kind_effect_violated"),
        ("fx_mutate_silently", "conservation_violated"),
    ],
)
async def test_each_step_failure_code_reaches_002s_callback(
    client: httpx.AsyncClient,
    real_driver: RealDriver,
    data_dir: Path,
    operator_name: str,
    operator: str,
    code: str,
) -> None:
    """6.7: the executor publishes only an error meta.json; 002 fails the build with the code."""
    job_id = await _start(client, data_dir, (operator, {}))
    assert real_driver.run(job_id) == "failed"
    assert _result(job_id)["result"]["error"]["code"] == code
    with sync_session_factory()() as db:
        step = db.query(StepExecution).filter_by(job_id=job_id, kind="operator").one()
        assert step.state == "failed" and step.error["code"] == code
        assert not part_files(data_dir / step.output_dir)


async def test_an_unexpected_exception_is_a_failed_step_not_a_hang(
    client: httpx.AsyncClient,
    real_driver: RealDriver,
    data_dir: Path,
    operator_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.operators.native import fixtures

    def explode(self: Any, batch: Any, params: Any, ctx: Any) -> Any:
        raise ZeroDivisionError("bad maths")

    monkeypatch.setattr(fixtures.KeepAll, "run", explode)
    job_id = await _start(client, data_dir, ("fx_keep_all", {}))
    assert real_driver.run(job_id) == "failed"
    error = _result(job_id)["result"]["error"]
    assert error["code"] == "step_failed" and "ZeroDivisionError" in error["message"]


async def test_cancel_mid_step_keeps_completed_batches_in_staging(
    client: httpx.AsyncClient,
    real_driver: RealDriver,
    data_dir: Path,
    operator_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """6.5: cancel at a batch boundary; staged batches kept; the job ends cancelled."""
    monkeypatch.setattr(get_settings(), "operator_batch_rows", 3)
    job_id = await _start(client, data_dir, ("fx_keep_all", {}))
    from src.workers import version_build_tasks

    version_build_tasks.run_pass(job_id)  # assemble, then dispatch step 1
    real_driver.sent.clear()
    assert len(real_driver.steps) == 1

    batches = {"n": 0}
    from src.operators.native import fixtures

    original = fixtures.KeepAll.run

    def counting(self: Any, batch: Any, params: Any, ctx: Any) -> Any:
        batches["n"] += 1
        if batches["n"] == 1:  # the operator asks to stop after the first batch
            with sync_session_factory()() as db:
                plan = cancellation.plan_cancel("running")
                db.execute(
                    text(
                        "UPDATE dw_jobs SET status = :s, cancel_requested_at = now() WHERE id = :id"
                    ),
                    {"s": plan.new_status, "id": job_id},
                )
                db.commit()
        return original(self, batch, params, ctx)

    monkeypatch.setattr(fixtures.KeepAll, "run", counting)
    monkeypatch.setattr(cancellation, "DEFAULT_POLL_INTERVAL_S", 0.0)
    before = (
        set((data_dir / "staging").glob("step-*")) if (data_dir / "staging").exists() else set()
    )
    link_args = real_driver.run_step()
    assert real_driver.step_results[-1]["status"] == "cancelled"
    assert batches["n"] == 1, "the step stopped at the next batch boundary"
    staged = set((data_dir / "staging").glob("step-*")) - before
    assert len(staged) == 1
    kept = pq.read_table(next(iter(staged)) / "part-00000.parquet")
    assert kept.num_rows == 3, "the completed batch stays in staging"
    assert link_args == [job_id]
    version_build_tasks.run_pass(job_id)  # the link: 002's advance sees the request
    assert real_driver.status(job_id) == "cancelled"
    with sync_session_factory()() as db:
        step = db.query(StepExecution).filter_by(job_id=job_id, kind="operator").one()
        assert step.state == "cancelled"


async def test_a_long_step_heartbeat_keeps_the_janitor_away(
    client: httpx.AsyncClient, real_driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    """6.9: the step's heartbeat (not the build task's) is what the janitor reads."""
    from datetime import timedelta

    from src.core.clock import utc_now
    from src.workers import janitor, version_build_tasks

    job_id = await _start(client, data_dir, ("fx_keep_all", {}))
    version_build_tasks.run_pass(job_id)
    with sync_session_factory()() as db:
        step = db.query(StepExecution).filter_by(job_id=job_id, kind="operator").one()
        step_id = step.id
        old = utc_now() - timedelta(hours=3)
        db.execute(
            text("UPDATE dw_jobs SET heartbeat_at = :t, started_at = :t WHERE id = :id"),
            {"t": old, "id": job_id},
        )
        db.execute(
            text("UPDATE dw_step_executions SET heartbeat_at = :t WHERE id = :id"),
            {"t": old, "id": step_id},
        )
        db.commit()
    beat = executor.Heartbeat(job_id, step_id, "Step fx_keep_all@1")
    assert beat.tick(10, 100, force=True)
    with sync_session_factory()() as db:
        assert janitor.sweep(db, active_ids=set()) == []
    assert real_driver.status(job_id) == "running"
    # and without the heartbeat the same build IS reaped: the test can fail
    with sync_session_factory()() as db:
        db.execute(
            text("UPDATE dw_step_executions SET heartbeat_at = :t WHERE id = :id"),
            {"t": old, "id": step_id},
        )
        db.execute(
            text("UPDATE dw_jobs SET heartbeat_at = :t WHERE id = :id"), {"t": old, "id": job_id}
        )
        db.commit()
    with sync_session_factory()() as db:
        assert [r.job_id for r in janitor.sweep(db, active_ids=set())] == [job_id]


def test_heartbeat_is_throttled_on_time(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []
    monkeypatch.setattr(executor, "record_progress", lambda *a, **k: calls.append(k) or True)
    beat = executor.Heartbeat("job-x", None, "Step")
    assert beat.tick(1, None) is True
    assert beat.tick(2, None) is False, "within the interval nothing is written"
    monkeypatch.setattr(get_settings(), "progress_heartbeat_seconds", 0.0)
    assert beat.tick(3, None) is True
    assert len(calls) == 2


async def test_split_roles_from_the_real_executor_reach_the_version(
    client: httpx.AsyncClient, real_driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    """FR-003.27 across the boundary: meta.json's split_roles become the version's held-out split."""
    job_id = await _start(client, data_dir, ("fx_split_half", {}))
    assert real_driver.run(job_id) == "completed"
    version_id = _result(job_id)["result"]["version_id"]
    with get_sync_engine().connect() as conn:
        splits = conn.execute(
            text("SELECT splits FROM dw_versions WHERE id = :id"), {"id": version_id}
        ).scalar_one()
    held = {s["name"]: s["held_out"] for s in splits}
    assert held == {"train": False, "test": True}
