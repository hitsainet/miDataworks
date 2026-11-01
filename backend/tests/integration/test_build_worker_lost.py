"""A version build whose step worker died (002; audited 2026-10-18 with the 005 FTASKS 9.5 and 007
reap-hook fixes).

A build dispatches its step to an operator worker and returns; the janitor reads the step's
heartbeat (``build_liveness``). When that worker is SIGKILLed the janitor reaps the build job, but
before this fix nothing settled the build's step executions: the step stayed ``running`` and kept
its identity's live slot (``uq_dw_step_executions_identity_live``). Building the same request again
found that execution, owned by the dead job, and waited on it for good — the advertised "building
the same request again resumes from completed steps" could never happen.

These tests run the REAL janitor sweep with the live registry.
"""

from __future__ import annotations

import importlib
from datetime import timedelta
from pathlib import Path

import httpx
from sqlalchemy import select, text, update

from src.core.clock import utc_now
from src.core.database import get_sync_engine, sync_session_factory
from src.models import Job, StepExecution
from src.workers import janitor, version_build_tasks
from tests.integration.test_version_build import request, setup, src
from tests.support.stub_operators import body
from tests.support.version_fixtures import HUMOR_TRAIN, BuildDriver, driver, make_source

__all__ = ["driver"]


def steps(job_id: str) -> list[tuple[str, str]]:
    with sync_session_factory()() as db:
        return [
            (s.kind, s.state)
            for s in db.execute(
                select(StepExecution)
                .where(StepExecution.job_id == job_id)
                .order_by(StepExecution.created_at)
            ).scalars()
        ]


def kill_step_worker(driver: BuildDriver, job_id: str) -> None:
    """The operator worker running the build's step dies: its task is gone (never run), and both
    heartbeats age past the limit."""
    driver.stubs.pending.clear()
    old = utc_now() - timedelta(hours=2)
    with get_sync_engine().begin() as conn:
        conn.execute(update(Job).where(Job.id == job_id).values(heartbeat_at=old, started_at=old))
        conn.execute(
            update(StepExecution).where(StepExecution.job_id == job_id).values(heartbeat_at=old)
        )


def test_the_build_reap_hooks_and_reconciler_are_in_the_live_registry() -> None:
    from src.core.celery_app import TASK_MODULES

    for module in TASK_MODULES:
        importlib.import_module(module)
    for kind in ("version_build", "version_verify"):
        assert janitor.ON_REAP[kind] is version_build_tasks.reap_build, kind
    assert janitor.RECONCILERS["step_executions"] is version_build_tasks.reconcile_step_executions


async def test_a_build_whose_step_worker_died_can_be_built_again(
    client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
) -> None:
    source = make_source(data_dir, {"train": HUMOR_TRAIN})
    ds, rev = await setup(client, body(("stub_keep", {})))
    job_id = (await request(client, ds, rev, [src(source)], seed=3)).json()["job_id"]
    version_build_tasks.run_pass(job_id)  # assembles step 0, dispatches step 1, returns
    assert steps(job_id) == [("assemble", "completed"), ("operator", "running")]
    kill_step_worker(driver, job_id)

    with sync_session_factory()() as db:
        reaped = janitor.sweep(db, active_ids=set())
    assert [r.job_id for r in reaped] == [job_id]
    assert driver.status(job_id) == "failed"

    again = await request(client, ds, rev, [src(source)], seed=3)
    assert again.status_code == 202, again.text
    second = again.json()["job_id"]
    assert second != job_id
    # the live symptom before the fix: the new build waited on the dead job's step for good
    assert driver.run(second) == "completed"
    # the dead step no longer holds its identity; the completed assemble was kept and reused
    assert steps(job_id) == [("assemble", "completed"), ("operator", "failed")]
    assert steps(second) == [("operator", "completed")]


async def test_a_cancelling_build_reaped_cancels_its_steps(
    client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
) -> None:
    source = make_source(data_dir, {"train": HUMOR_TRAIN})
    ds, rev = await setup(client, body(("stub_keep", {})))
    job_id = (await request(client, ds, rev, [src(source)], seed=4)).json()["job_id"]
    version_build_tasks.run_pass(job_id)
    with sync_session_factory()() as db:
        db.execute(
            text("UPDATE dw_jobs SET status='cancelling', cancel_requested_at=now() WHERE id=:j"),
            {"j": job_id},
        )
        db.commit()
    kill_step_worker(driver, job_id)
    with sync_session_factory()() as db:
        assert [r.status for r in janitor.sweep(db, active_ids=set())] == ["cancelled"]
    assert steps(job_id) == [("assemble", "completed"), ("operator", "cancelled")]


async def test_steps_stranded_before_the_hook_existed_are_reconciled(
    client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
) -> None:
    """The production end state before this fix: the job already reaped (failed), its step still
    ``running``. The sweep's reconciliation frees it; a step of a live build is never touched, nor
    one whose job ended within the grace."""
    ds, rev = await setup(client, body(("stub_keep", {})))
    started: list[str] = []
    for _ in range(3):  # a source each, so no step is shared between the three builds
        source = make_source(data_dir, {"train": HUMOR_TRAIN})
        job_id = (await request(client, ds, rev, [src(source)], seed=5)).json()["job_id"]
        version_build_tasks.run_pass(job_id)
        driver.stubs.pending.clear()
        started.append(job_id)
    stranded, recent, live = started
    with sync_session_factory()() as db:
        for job_id, minutes in ((stranded, 30), (recent, 0)):
            db.execute(
                text(
                    "UPDATE dw_jobs SET status='failed', error='The worker stopped reporting', "
                    "completed_at = now() - make_interval(mins => :m) WHERE id = :j"
                ),
                {"j": job_id, "m": minutes},
            )
        db.commit()
    with sync_session_factory()() as db:
        assert janitor.sweep(db, active_ids=set()) == []  # nothing reaped: reconciliation only
    assert steps(stranded) == [("assemble", "completed"), ("operator", "failed")]
    assert steps(recent) == [("assemble", "completed"), ("operator", "running")]
    assert steps(live) == [("assemble", "completed"), ("operator", "running")]
    with sync_session_factory()() as db:
        later = version_build_tasks.stranded_executions(db, now=utc_now() + timedelta(days=1))
    assert {job for _, job in later} == {recent}


async def test_a_build_waiting_on_the_dead_step_is_advanced_once_and_completes(
    client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
) -> None:
    """Build B shares its first step with build A and waits on A's execution. A's step worker
    dies; the reap settles A's step and sends B exactly one advance (payload: B's job), and B then
    computes the step itself."""
    source = make_source(data_dir, {"train": HUMOR_TRAIN})
    ds, rev_a = await setup(client, body(("stub_keep", {})))
    rec_b = await client.post(
        "/api/v1/recipes",
        json={"name": "humor-b", "body": body(("stub_keep", {}), ("stub_keep", {}))},
    )
    rev_b = rec_b.json()["head_revision_id"]
    a = (await request(client, ds, rev_a, [src(source)], seed=3)).json()["job_id"]
    version_build_tasks.run_pass(a)
    b = (await request(client, ds, rev_b, [src(source)], seed=3)).json()["job_id"]
    assert version_build_tasks.run_pass(b)["outcome"] == "waiting"
    kill_step_worker(driver, a)
    driver.sent.clear()

    with sync_session_factory()() as db:
        assert [r.job_id for r in janitor.sweep(db, active_ids=set())] == [a]
    advances = [s for s in driver.sent if s[0] == "midataworks.versions.advance_build"]
    assert advances == [("midataworks.versions.advance_build", [b])]
    assert driver.run(b) == "completed"
