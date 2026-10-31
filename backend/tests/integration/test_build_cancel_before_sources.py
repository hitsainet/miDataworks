"""002 mutation round (2026-10-17), survivor V7: ``advance`` checks the cancel request BEFORE any
work. Without that check a cancelled build still hashed every source file (``verify_files``, the
longest fixed cost of a build) before the in-loop checker stopped it; the old test cancelled only
after the sources were verified, so deleting the check stayed green."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from src.core.database import sync_session_factory
from src.models import Job, Version
from src.services import build_orchestrator
from src.workers import version_build_tasks
from tests.integration.test_version_build import request, setup, src
from tests.support.stub_operators import body
from tests.support.version_fixtures import HUMOR_TRAIN, BuildDriver, driver, make_source

__all__ = ["driver"]


async def test_a_cancel_before_the_first_pass_hashes_no_source(
    client: httpx.AsyncClient,
    driver: BuildDriver,
    data_dir: Path,
    operator_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = make_source(data_dir, {"train": HUMOR_TRAIN})
    ds, rev = await setup(client, body(("stub_keep", {})))
    job_id = (await request(client, ds, rev, [src(source)], seed=4)).json()["job_id"]
    with sync_session_factory()() as db:  # claimed by a worker, then cancelled by the operator
        db.execute(
            text(
                "UPDATE dw_jobs SET status='cancelling', started_at=now(), "
                "cancel_requested_at=now() WHERE id=:id"
            ),
            {"id": job_id},
        )
        db.commit()
    hashed: list[Any] = []
    real = build_orchestrator.verify_files
    monkeypatch.setattr(
        build_orchestrator, "verify_files", lambda *a, **k: hashed.append(a) or real(*a, **k)
    )
    version_build_tasks.run_pass(job_id)
    with sync_session_factory()() as db:
        assert db.get(Job, job_id).status == "cancelled"
        assert db.query(Version).count() == 0
    assert hashed == []
