"""Publish builds, check runs and exports whose worker died (008; audited 2026-10-18 with the
005 FTASKS 9.5 and 007 reap-hook fixes).

Each of these records is settled by its own worker. When that worker is SIGKILLed the janitor
reaps the job, but before this fix no ``publish_build`` / ``publish_check`` / ``export`` hook
existed, so the record kept its live status for good:

- a build stuck ``building`` was JOINED by every later request for the same projection
  (``request_build`` returns a queued or building build as-is), so it could never be built again;
- an export stuck ``running`` and a check run stuck ``queued`` each made the version undeletable
  (``version_in_use`` from 008's delete guards).

The janitor here is the REAL sweep with the live registry. The publish build's worker is killed
with a real SIGKILL in a forked child, because its task catches ``BaseException`` and so no
in-process stand-in can model a kill.
"""

from __future__ import annotations

import importlib
import os
import signal
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from src.core.clock import utc_now
from src.core.database import get_sync_engine, sync_session_factory
from src.workers import janitor, publish_tasks
from tests.integration.publishing.test_trl_export import validator, version_of
from tests.support.publish_fixtures import (
    PublishDriver,
    built_version,
    completed_build,
    publisher,
    store_token,
)
from tests.support.version_fixtures import BuildDriver, driver

__all__ = ["driver", "publisher"]

KINDS = ("publish_build", "publish_check", "export")
LIMITS = {kind: timedelta(seconds=1) for kind in KINDS}


class SimulatedCrash(BaseException):
    """Stands in for SIGKILL where the task has no ``except BaseException``."""


def age_job(job_id: str) -> None:
    with sync_session_factory()() as db:
        db.execute(
            text(
                "UPDATE dw_jobs SET heartbeat_at = now() - interval '30 minutes', "
                "started_at = now() - interval '30 minutes' WHERE id = :j"
            ),
            {"j": job_id},
        )
        db.commit()


def sweep() -> list[tuple[str, str]]:
    with sync_session_factory()() as db:
        return [(r.job_id, r.status) for r in janitor.sweep(db, active_ids=set(), limits=LIMITS)]


def row(table: str, row_id: str) -> dict[str, Any]:
    """One record by id; ``table`` is a constant of this module, never input."""
    query = text(f"SELECT * FROM {table} WHERE id = :i")  # noqa: S608
    with sync_session_factory()() as db:
        return dict(db.execute(query, {"i": row_id}).mappings().one())


def job_of(table: str, row_id: str) -> str:
    return str(row(table, row_id)["job_id"])


def request_cancel(job_id: str) -> None:
    with sync_session_factory()() as db:
        db.execute(
            text("UPDATE dw_jobs SET status='cancelling', cancel_requested_at=now() WHERE id=:j"),
            {"j": job_id},
        )
        db.commit()


def sigkill_build_worker(job_id: str) -> None:
    """Run the real build task in a forked child and SIGKILL it at its first batch: nothing after
    the kill runs (no ``except``, no ``finally``), as on the deployed worker."""
    pid = os.fork()
    if pid == 0:  # the child: fresh connections, then the real task until the kill
        try:
            get_sync_engine().dispose(close=False)
            from src.services.publishing import build_service

            original = build_service.run_build

            def killed(db: Any, build_id: str, **_: Any) -> Any:
                return original(db, build_id, on_batch=lambda: os.kill(os.getpid(), signal.SIGKILL))

            build_service.run_build = killed  # type: ignore[assignment]
            publish_tasks.run_build_job(job_id)
        finally:
            os._exit(3)  # never reached when the kill lands
    _, status = os.waitpid(pid, 0)
    assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL, status


def test_the_publish_reap_hooks_and_reconciler_are_in_the_live_registry() -> None:
    from src.core.celery_app import TASK_MODULES

    for module in TASK_MODULES:
        importlib.import_module(module)
    for kind in KINDS:
        assert janitor.ON_REAP[kind] is publish_tasks.reap_publish_record, kind
    assert janitor.ON_REAP["publish"] is publish_tasks.reap_publish
    assert janitor.RECONCILERS["publish_records"] is publish_tasks.reconcile_publish_records


@pytest.mark.parametrize(("cancel_first", "expected"), [(False, "failed"), (True, "cancelled")])
async def test_a_killed_build_worker_frees_the_projection_for_a_new_build(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
    cancel_first: bool,
    expected: str,
) -> None:
    version_id = await built_version(client, driver, data_dir)
    first = await client.post(
        f"/api/v1/versions/{version_id}/publish-builds", json={"label_column": "label"}
    )
    build_id = first.json()["build_id"]
    publisher.sent.clear()
    job_id = job_of("dw_publish_builds", build_id)
    sigkill_build_worker(job_id)
    assert row("dw_publish_builds", build_id)["status"] == "building"
    if cancel_first:
        request_cancel(job_id)
    age_job(job_id)

    assert sweep() == [(job_id, expected)]
    settled = row("dw_publish_builds", build_id)
    assert settled["status"] == expected
    assert (
        settled["error"]["code"] == "worker_lost" and "no heartbeat" in settled["error"]["message"]
    )

    again = await client.post(
        f"/api/v1/versions/{version_id}/publish-builds", json={"label_column": "label"}
    )
    assert again.status_code == 202, again.text  # a new build, not the dead one joined
    assert again.json()["build_id"] != build_id
    publisher.run_publish_tasks()
    rebuilt = await client.get(f"/api/v1/publish-builds/{again.json()['build_id']}")
    assert rebuilt.json()["status"] == "completed"


async def test_a_killed_export_worker_no_longer_pins_the_version(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.services.exports import export_service

    validator(monkeypatch)
    version_id = await version_of(client, driver, data_dir, "dpo")
    response = await client.post(
        "/api/v1/exports", json={"target": "trl", "version_id": version_id, "trl_type": "dpo"}
    )
    export_id = response.json()["export_id"]
    publisher.sent.clear()
    job_id = job_of("dw_exports", export_id)

    def die(*_a: Any, **_k: Any) -> Any:
        raise SimulatedCrash()

    with monkeypatch.context() as m:
        m.setattr(export_service, "_run", die)
        with pytest.raises(SimulatedCrash):
            publish_tasks.run_export_job(job_id)
    assert row("dw_exports", export_id)["status"] == "running"
    age_job(job_id)

    assert sweep() == [(job_id, "failed")]
    settled = row("dw_exports", export_id)
    assert settled["status"] == "failed" and settled["error"]["code"] == "worker_lost"
    assert settled["completed_at"] is not None
    deleted = await client.request(
        "DELETE", f"/api/v1/versions/{version_id}", json={"reason": "superseded"}
    )
    assert deleted.status_code == 200, deleted.text


async def test_a_killed_check_worker_no_longer_pins_the_version(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.services.publishing import publish_job

    await store_token(client)
    version_id = await built_version(client, driver, data_dir)
    build = await completed_build(client, publisher, version_id)
    response = await client.post(
        f"/api/v1/versions/{version_id}/publish-checks",
        json={"build_id": build["id"], "repo_id": "mistudio/humor-test", "visibility": "public"},
    )
    run_id = response.json()["check_run_id"]
    publisher.sent.clear()
    job_id = job_of("dw_publish_check_runs", run_id)

    def die(*_a: Any, **_k: Any) -> Any:
        raise SimulatedCrash()

    with monkeypatch.context() as m:
        m.setattr(publish_job, "run_check_run", die)
        with pytest.raises(SimulatedCrash):
            publish_tasks.run_check_job(job_id)
    assert row("dw_publish_check_runs", run_id)["status"] == "queued"
    age_job(job_id)

    assert sweep() == [(job_id, "failed")]
    assert row("dw_publish_check_runs", run_id)["status"] == "failed"
    deleted = await client.request(
        "DELETE", f"/api/v1/versions/{version_id}", json={"reason": "superseded"}
    )
    assert deleted.status_code == 200, deleted.text


async def test_records_left_live_by_an_ended_job_are_reconciled(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    """The production end state before this fix (the job reaped by a janitor with no hook), and
    the cancel-before-start path (the task skips a cancelled job without settling its record):
    the sweep's reconciliation settles both. A record whose job ended within the grace, or whose
    job is live, is left alone."""
    version_id = await built_version(client, driver, data_dir)
    ids = []
    for label in ("label", None):
        made = await client.post(
            f"/api/v1/versions/{version_id}/publish-builds", json={"label_column": label}
        )
        ids.append(made.json()["build_id"])
    other_version = await built_version(client, driver, data_dir, name="humor-two")
    live = await client.post(f"/api/v1/versions/{other_version}/publish-builds", json={})
    ids.append(live.json()["build_id"])  # its job stays queued: live
    publisher.sent.clear()
    stranded, recent, live_id = ids
    with sync_session_factory()() as db:
        for build_id, status, minutes in ((stranded, "failed", 30), (recent, "cancelled", 0)):
            db.execute(
                text(
                    "UPDATE dw_jobs SET status = :s, completed_at = now() - "
                    "make_interval(mins => :m) WHERE id = (SELECT job_id FROM dw_publish_builds "
                    "WHERE id = :b)"
                ),
                {"s": status, "m": minutes, "b": build_id},
            )
        db.execute(
            text("UPDATE dw_publish_builds SET status = 'building' WHERE id = :b"), {"b": stranded}
        )
        db.commit()

    assert sweep() == []  # nothing reaped: reconciliation only
    assert row("dw_publish_builds", stranded)["status"] == "failed"
    assert "without settling" in row("dw_publish_builds", stranded)["error"]["message"]
    assert row("dw_publish_builds", recent)["status"] == "queued"
    assert row("dw_publish_builds", live_id)["status"] == "queued"
    with sync_session_factory()() as db:
        later = publish_tasks.orphaned_publish_records(db, now=utc_now() + timedelta(days=1))
    assert later == [("publish_build", recent)]
