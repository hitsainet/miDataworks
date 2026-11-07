"""A generation run whose worker died (007; the same defect as 005 FTASKS 9.5, found by audit on
2026-10-18 after the label-run fix in ``587cd96``).

A SIGKILLed generation worker leaves its job ``running`` until the janitor reaps it. Before this
fix no ``generation_run`` reap hook existed, so the reap failed the JOB and left the RUN
``running`` for good: cancel answered ``RUN_NOT_CANCELLABLE`` (no live job), resume answered
``RUN_NOT_RESUMABLE`` (the run is not failed or cancelled), and the run's miLLM lease waited for
Beat's ``renew_all``. A minimal-pair chain whose stage is that run stayed ``running`` with it.

These tests run the REAL janitor sweep with the live registry, never a helper that writes the
run's state.
"""

from __future__ import annotations

import importlib
from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text

from src.core.clock import utc_now
from src.core.database import sync_session_factory
from src.models.generation import GenerationRecord
from src.services.model_lease_holder import HOLDER
from src.workers import janitor
from tests.support.generation_fixtures import (
    Gen,
    make_version,
    respond_template,
    run_body,
    version_table,
)

RUNS = "/api/v1/generation-runs"
LIMIT = {"generation_run": timedelta(seconds=1)}
LEASE_PATH = "/api/models/7/lease"


class SimulatedCrash(BaseException):
    """Stands in for SIGKILL: escapes every ``except Exception`` and every ``except`` the engine
    has for its own stops, like a dead worker would."""


def age_job(job_id: str, minutes: int = 30) -> None:
    with sync_session_factory()() as db:
        db.execute(
            text(
                "UPDATE dw_jobs SET heartbeat_at = now() - make_interval(mins => :m), "
                "started_at = now() - make_interval(mins => :m) WHERE id = :j"
            ),
            {"j": job_id, "m": minutes},
        )
        db.commit()


def sweep() -> list[str]:
    with sync_session_factory()() as db:
        return [r.job_id for r in janitor.sweep(db, active_ids=set(), limits=LIMIT)]


def request_cancel(job_id: str) -> None:
    with sync_session_factory()() as db:
        db.execute(
            text("UPDATE dw_jobs SET status='cancelling', cancel_requested_at=now() WHERE id=:j"),
            {"j": job_id},
        )
        db.commit()


def records(run_id: str) -> list[int]:
    with sync_session_factory()() as db:
        return list(
            db.execute(
                select(GenerationRecord.record_index)
                .where(GenerationRecord.run_id == run_id)
                .order_by(GenerationRecord.record_index)
            ).scalars()
        )


def crash_like_sigkill(
    gen: Gen, monkeypatch: pytest.MonkeyPatch, job_id: str, at_position: int
) -> None:
    """Kill the job at a unit boundary the way SIGKILL does: no ``except``, no ``finally`` — so
    the engine's ``holder.leave`` on the way out does not run either."""

    def die(position: int) -> None:
        if position == at_position:
            raise SimulatedCrash()

    gen.deps_overrides["on_unit"] = die
    with monkeypatch.context() as m:
        m.setattr(HOLDER, "leave", lambda *_a, **_k: None)
        with pytest.raises(SimulatedCrash):
            gen.run_job(job_id)
    gen.deps_overrides.pop("on_unit")


async def start(client: httpx.AsyncClient, n: int) -> str:
    version = make_version(version_table([f"q {i:02d}" for i in range(n)]))
    template = await respond_template(client)
    response = await client.post(RUNS, json=run_body(version, template, sample_size=n))
    assert response.status_code == 202, response.text
    return str(response.json()["id"])


def test_the_generation_reap_hook_and_reconciler_are_in_the_live_registry() -> None:
    """Read after importing exactly the modules the Celery app includes (the janitor runs in a
    worker that imports them all), so a hook defined but never registered fails here."""
    from src.core.celery_app import TASK_MODULES

    for module in TASK_MODULES:
        importlib.import_module(module)
    from src.workers import generation_tasks

    assert janitor.ON_REAP["generation_run"] is generation_tasks.reap_generation_run
    assert janitor.RECONCILERS["generation_runs"] is generation_tasks.reconcile_generation_runs


@pytest.mark.parametrize(
    ("cancel_first", "expected_state"), [(False, "failed"), (True, "cancelled")]
)
async def test_a_killed_worker_is_reaped_into_a_resumable_run_and_its_lease_released(
    client: httpx.AsyncClient,
    gen: Gen,
    monkeypatch: pytest.MonkeyPatch,
    cancel_first: bool,
    expected_state: str,
) -> None:
    """Killed mid-run after one committed chunk (optionally after an operator cancel nobody was
    left to see). The janitor's reap moves the run to a state the resume path accepts, keeps the
    committed records, releases the lease at once (one DELETE, carrying the lease the job took),
    and a resume finishes every record exactly once."""
    from src.core.config import get_settings

    monkeypatch.setattr(get_settings(), "generation_chunk_size", 2)
    run_id = await start(client, 6)
    job_id = gen.job_for(run_id)
    crash_like_sigkill(gen, monkeypatch, job_id, at_position=3)
    assert gen.run(run_id).state == "running"
    assert records(run_id) == [0, 1]  # one committed chunk
    (lease_id,) = list(gen.fake.leases)  # held: no finally ran
    assert gen.fake.calls(LEASE_PATH, "DELETE") == []
    # the live symptom before the reap: nothing to cancel, nothing to resume
    if cancel_first:
        request_cancel(job_id)
    refused = await client.post(f"{RUNS}/{run_id}/resume")
    assert refused.json()["error"]["code"] == "RUN_NOT_RESUMABLE"

    age_job(job_id)
    assert sweep() == [job_id]

    stuck = gen.run(run_id)
    assert stuck.state == expected_state
    assert stuck.error is not None and stuck.error["code"] == "WORKER_LOST"
    assert "no heartbeat" in stuck.error["message"]
    assert stuck.failure_reason is None, "a lost worker is resumable"
    assert stuck.completed_at is not None
    assert records(run_id) == [0, 1]  # committed records kept
    deletes = gen.fake.calls(LEASE_PATH, "DELETE")
    assert len(deletes) == 1 and deletes[0].headers["x-millm-lease"] == lease_id
    assert gen.fake.leases == {}
    detail = (await client.get(f"{RUNS}/{run_id}")).json()
    assert detail["resumable"] is True

    response = await client.post(f"{RUNS}/{run_id}/resume")
    assert response.status_code == 200, response.text
    gen.run_job(gen.job_for(run_id))
    final = gen.run(run_id)
    assert final.state == "completed", final.error
    assert records(run_id) == list(range(6))
    # a second sweep finds nothing more to do
    assert sweep() == []
    assert gen.run(run_id).state == "completed"


def stick(run_id: str, job_id: str, job_status: str, ended_minutes_ago: int) -> None:
    """The production end state: the job already reaped by a janitor that had no hook, the run
    still ``running``."""
    with sync_session_factory()() as db:
        db.execute(
            text(
                "UPDATE dw_jobs SET status = :s, error = 'The worker stopped reporting: no "
                "heartbeat', completed_at = now() - make_interval(mins => :m) WHERE id = :j"
            ),
            {"j": job_id, "s": job_status, "m": ended_minutes_ago},
        )
        db.execute(
            text("UPDATE dw_generation_runs SET state = 'running' WHERE id = :r"), {"r": run_id}
        )
        db.commit()


async def test_runs_stuck_before_the_hook_existed_are_reconciled_by_the_sweep(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    """After deploy the hook never fires again for runs whose job was reaped earlier; the sweep's
    reconciliation applies the same transition. Left alone: a run whose job ended within the
    grace (the API's own two-commit cancel), and a run with a live job."""
    old_cancelled = await start(client, 2)
    old_failed = await start(client, 2)
    recent = await start(client, 2)
    live = await start(client, 2)
    stick(old_cancelled, gen.job_for(old_cancelled), "cancelled", 30)
    stick(old_failed, gen.job_for(old_failed), "failed", 11 * 24 * 60)
    stick(recent, gen.job_for(recent), "cancelled", 0)
    with sync_session_factory()() as db:
        db.execute(
            text("UPDATE dw_generation_runs SET state = 'running' WHERE id = :r"), {"r": live}
        )
        db.execute(
            text(
                "UPDATE dw_jobs SET status = 'running', heartbeat_at = now() + interval '1 hour' "
                "WHERE id = :j"
            ),
            {"j": gen.job_for(live)},
        )
        db.commit()

    for run_id in (old_cancelled, old_failed):
        refused = await client.post(f"{RUNS}/{run_id}/resume")
        assert refused.json()["error"]["code"] == "RUN_NOT_RESUMABLE"

    assert sweep() == []  # nothing reaped: this is the reconciliation, not the hook
    assert gen.run(old_cancelled).state == "cancelled"
    assert gen.run(old_failed).state == "failed"
    for run_id in (old_cancelled, old_failed):
        moved = gen.run(run_id)
        assert moved.error is not None and moved.error["code"] == "WORKER_LOST"
        assert "without updating the run" in moved.error["message"]
    assert gen.run(recent).state == "running"
    assert gen.run(live).state == "running"
    # past the grace the recent run is orphaned; the run with a live job never is (the grace must
    # not be what protects it)
    from src.services.generation.run_service import orphaned_runs

    with sync_session_factory()() as db:
        later = {r for r, _ in orphaned_runs(db, now=utc_now() + timedelta(days=1))}
    assert later == {recent}

    response = await client.post(f"{RUNS}/{old_failed}/resume")
    assert response.status_code == 200, response.text
    gen.run_job(gen.job_for(old_failed))
    assert gen.run(old_failed).state == "completed"
    assert records(old_failed) == [0, 1]


async def test_a_resumed_run_is_not_moved_by_its_old_jobs_reap(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    """The hook moves the run only when the reaped job is its LAST job: once a resume added a
    newer job, the run belongs to that one."""
    from src.services.generation.run_service import end_after_worker_lost

    run_id = await start(client, 2)
    first_job = gen.job_for(run_id)
    stick(run_id, first_job, "failed", 30)
    with sync_session_factory()() as db:
        db.execute(text("UPDATE dw_generation_runs SET state='failed' WHERE id=:r"), {"r": run_id})
        db.commit()
    assert (await client.post(f"{RUNS}/{run_id}/resume")).status_code == 200
    with sync_session_factory()() as db:
        assert end_after_worker_lost(db, first_job, "failed", "late reap") is None
    assert gen.run(run_id).state == "queued"


async def test_a_reap_whose_lease_is_shared_only_leaves_it(
    client: httpx.AsyncClient, gen: Gen, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-005.37: another job on the same model is still a member, so the reaped job leaves and
    the lease stays held (no DELETE) until the last member leaves."""
    from src.core.config import get_settings
    from src.services.label_run_service import caller_for

    monkeypatch.setattr(get_settings(), "generation_chunk_size", 2)
    run_id = await start(client, 4)
    job_id = gen.job_for(run_id)
    crash_like_sigkill(gen, monkeypatch, job_id, at_position=1)
    (lease_id,) = list(gen.fake.leases)
    with caller_for(gen.base_url, None) as caller:
        HOLDER.join(caller, gen.fake.resident["name"], "job_other")  # type: ignore[index]
    age_job(job_id)
    assert sweep() == [job_id]
    assert gen.run(run_id).state == "failed"
    assert gen.fake.calls(LEASE_PATH, "DELETE") == []
    assert list(gen.fake.leases) == [lease_id]

    def lease_caller(base_url: str) -> Any:
        return caller_for(base_url, None)

    assert HOLDER.leave_for("job_other", lease_caller) == 1
    deletes = gen.fake.calls(LEASE_PATH, "DELETE")
    assert len(deletes) == 1 and deletes[0].headers["x-millm-lease"] == lease_id
