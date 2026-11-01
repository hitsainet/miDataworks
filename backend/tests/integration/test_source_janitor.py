"""The janitor and a source import (001 FTASKS 7.9; FR-001.8).

A live import whose heartbeat advances through ``CancelWatchdog.on_tick`` is not reaped during a
long download; a stopped one is reaped, its source marked ``failed`` and its staging swept.
"""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import update

from src.core.clock import utc_now
from src.core.config import get_settings
from src.core.database import sync_session_factory
from src.models.job import Job
from src.workers import janitor
from tests.support import db_factories
from tests.support.hf_mock import COLBERT
from tests.support.source_fixtures import HfEnv, hf_env, source_row, sources_where

__all__ = ["hf_env"]
LIMIT = {"source_import": timedelta(seconds=3)}


def test_the_janitor_cleanup_hook_is_registered_for_source_import() -> None:
    from src.workers import source_tasks

    assert janitor.ON_REAP["source_import"] is source_tasks.reap_source_import


async def test_a_live_download_is_kept_alive_by_its_heartbeat(
    client: httpx.AsyncClient,
    hf_env: HfEnv,
    data_dir: Path,
    operator_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "progress_heartbeat_seconds", 0.5)
    hf_env.loader.slow = True
    job_id = (await client.post("/api/v1/sources/hf", json={"repo_id": COLBERT})).json()["job_id"]
    worker = threading.Thread(target=hf_env.run_imports, daemon=True)
    worker.start()
    try:
        await asyncio.sleep(6.5)  # longer than the 3 s limit: only on_tick heartbeats save it
        with sync_session_factory()() as db:
            reaped = janitor.sweep(db, active_ids=set(), limits=LIMIT)
        assert reaped == []
        assert hf_env.job(job_id).status == "running"
    finally:
        await client.post(f"/api/v1/jobs/{job_id}/cancel", json={"reason": "test over"})
        await asyncio.to_thread(worker.join, 20)


def test_a_stopped_import_is_reaped_its_source_failed_and_staging_swept(
    clean_db: None, data_dir: Path
) -> None:
    with sync_session_factory()() as db:
        job = db_factories.job(db, kind="source_import", status="running")
        source = db_factories.source(db, state="importing")
        source.import_job_id = job.id
        db.commit()
        old = utc_now() - timedelta(minutes=30)
        db.execute(update(Job).where(Job.id == job.id).values(heartbeat_at=old, started_at=old))
        db.commit()
        job_id, source_id = job.id, source.id
    staging = data_dir / "staging" / job_id
    cache = data_dir / "runs" / job_id / "hf_cache"
    for directory in (staging, cache):
        directory.mkdir(parents=True)
        (directory / "part.parquet").write_bytes(b"x")
    with sync_session_factory()() as db:
        reaped = janitor.sweep(db, active_ids=set(), limits=LIMIT)
    assert [r.job_id for r in reaped] == [job_id]
    row = source_row(source_id)
    assert row.state == "failed" and row.error["code"] == "worker_lost"
    assert not staging.exists() and not cache.exists()


def test_a_reap_hook_failure_does_not_stop_the_sweep(
    clean_db: None, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_a: object) -> None:
        raise RuntimeError("cleanup failed")

    monkeypatch.setitem(janitor.ON_REAP, "source_import", broken)
    with sync_session_factory()() as db:
        ids = []
        for _ in range(2):
            job = db_factories.job(db, kind="source_import", status="running")
            ids.append(job.id)
        db.commit()
        old = utc_now() - timedelta(minutes=30)
        db.execute(update(Job).where(Job.id.in_(ids)).values(heartbeat_at=old, started_at=old))
        db.commit()
    with sync_session_factory()() as db:
        reaped = janitor.sweep(db, active_ids=set(), limits=LIMIT)
    assert sorted(r.job_id for r in reaped) == sorted(ids)
    assert sources_where(state="ready") == []
    time.sleep(0)
