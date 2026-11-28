"""A label job's row moves with its run (FU-7a MCP gap (ae), 2026-11-27).

Live: while lr_M2 ran, ``dataworks_job_status`` read "1158 of 4430 rows labeled" with one
heartbeat for a minute while ``dataworks_get_label_run`` went from 1,400 to 2,400. The run read
counts committed labels, which advance at every chunk; the job row is written by
``record_progress``, whose progress-only writes are throttled to one per
``PROGRESS_HEARTBEAT_SECONDS`` (60 s), and the chunk boundary's write was throttled with the rest.
At 21 rows/s that is about 1,280 rows behind. The boundary now writes the job row every time, so
at each committed chunk the job read says exactly what the run read says. Rows between chunks keep
the throttle. The default 60 s throttle is left as it is here, so the run finishes inside one
throttle interval: on the old code every boundary after the first write was dropped.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from src.core.config import get_settings
from src.core.database import sync_session_factory
from src.models.job import Job
from src.models.label import Label
from src.services.label_run_engine import LabelRunEngine
from tests.integration.labeling.helpers import setup_classifier, start_body
from tests.support.labeling_fixtures import Labeling


async def test_every_chunk_boundary_writes_the_job_row(
    client: httpx.AsyncClient, labeling: Labeling, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert get_settings().progress_heartbeat_seconds == 60.0
    seen: list[dict[str, Any]] = []
    real = LabelRunEngine._boundary

    def boundary(self: LabelRunEngine, session: Any, loop: Any, *args: Any) -> None:
        real(self, session, loop, *args)
        with sync_session_factory()() as db:  # what a job read and a run read see now
            job = db.get(Job, loop.job_id)
            assert job is not None
            committed = db.execute(
                select(func.count()).select_from(Label).where(Label.label_run_id == loop.run.id)
            ).scalar_one()
            seen.append(
                {
                    "committed": int(committed),
                    "message": job.message,
                    "progress": job.progress,
                    "heartbeat_at": job.heartbeat_at,
                }
            )

    monkeypatch.setattr(LabelRunEngine, "_boundary", boundary)
    version_id, template_id = await setup_classifier(client, labeling, n=50)
    started = await client.post(
        "/api/v1/label-runs", json=start_body(version_id, template_id, chunk_size=10)
    )
    assert started.status_code == 201, started.text
    run = labeling.run_until_done(started.json()["id"])
    assert run.state == "completed"

    assert [s["committed"] for s in seen] == [10, 20, 30, 40, 50], seen
    for s in seen:
        assert s["message"] == f"{s['committed']} of 50 rows labeled.", s
        assert s["progress"] == pytest.approx(100.0 * s["committed"] / 50), s
    beats = [s["heartbeat_at"] for s in seen]
    assert all(b is not None for b in beats) and beats == sorted(beats)
    assert len(set(beats)) == len(beats), "each boundary stamped a new heartbeat"


async def test_a_committed_batch_writes_the_job_row(
    client: httpx.AsyncClient, labeling: Labeling, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The batch transport commits a whole batch at once; its job row follows the same rule."""
    seen: list[tuple[int, str | None]] = []
    real = LabelRunEngine._save_batches

    def save(self: LabelRunEngine, session: Any, run: Any, items: list[dict[str, Any]]) -> None:
        real(self, session, run, items)
        if any(i.get("state") == "committed" for i in items):
            with sync_session_factory()() as db:
                committed = db.execute(
                    select(func.count()).select_from(Label).where(Label.label_run_id == run.id)
                ).scalar_one()
                job = db.get(Job, _job_of(db, run.id))
                assert job is not None
                seen.append((int(committed), job.message))

    monkeypatch.setattr(LabelRunEngine, "_save_batches", save)
    version_id, template_id = await setup_classifier(client, labeling, n=120)
    started = await client.post(
        "/api/v1/label-runs", json=start_body(version_id, template_id, transport="batch")
    )
    assert started.status_code == 201, started.text
    assert labeling.run_until_done(started.json()["id"]).state == "completed"
    assert seen and seen[-1][0] == 120, seen
    for committed, message in seen:
        assert message == f"{committed} of 120 rows labeled.", seen


def _job_of(db: Any, run_id: str) -> str:
    from src.models.label_run import LabelRunJob

    link = db.execute(select(LabelRunJob).where(LabelRunJob.label_run_id == run_id)).scalars()
    return str(next(iter(link)).job_id)
