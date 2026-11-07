"""What a running generation run shows (017 FTASKS 17.8 (a); ENH-006 D94).

The operator saw a running carrier run read "0 generated · 0 discarded": its ``counts`` were
``{}`` until it finished. The worker now writes the run's counts after every committed chunk, and
the run carries its live job's progress and message; both are read mid-run here through the REAL
worker and the REST routes (one run and the list the Generation page tiles read).
The carrier half of 17.8 ((c) template names, (d) the lease) is
``tests/integration/behaviour/test_carrier_run_tile_17_8.py``.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from src.core.config import get_settings
from src.core.database import sync_session_factory
from src.models.generation import GenerationRun
from src.models.job import Job
from src.services.model_lease_holder import HOLDER
from tests.support.generation_fixtures import (
    Gen,
    make_version,
    respond_template,
    version_table,
)
from tests.support.generation_fixtures import run_body as standard_body

RUNS = "/api/v1/generation-runs"
CHUNK = 2
N = 6


class Stop(BaseException):
    """Stops the worker mid-run like a pod kill: past every ``except`` the engine has."""


def snapshot(run_id: str, job_id: str) -> tuple[dict[str, Any], float, str | None]:
    with sync_session_factory()() as db:
        run = db.get(GenerationRun, run_id)
        job = db.get(Job, job_id)
        assert run is not None and job is not None
        return dict(run.counts), float(job.progress), job.message


@pytest.fixture
def chunked(monkeypatch: pytest.MonkeyPatch, gen: Gen) -> Gen:
    monkeypatch.setattr(get_settings(), "generation_chunk_size", CHUNK)
    monkeypatch.setattr(get_settings(), "progress_heartbeat_seconds", 0.0)
    return gen


async def test_a_running_run_shows_its_counts_and_progress(
    client: httpx.AsyncClient, chunked: Gen, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = make_version(version_table([f"q {i:02d}" for i in range(N)]))
    template = await respond_template(client)
    response = await client.post(RUNS, json=standard_body(version, template, sample_size=N))
    assert response.status_code == 202, response.text
    run_id = str(response.json()["id"])
    job_id = chunked.job_for(run_id)
    seen: list[tuple[dict[str, Any], float, str | None]] = []

    def at_unit(_position: int) -> None:
        seen.append(snapshot(run_id, job_id))
        if len(seen) == 2 * CHUNK + 1:  # first unit of the third chunk: two chunks committed
            raise Stop()

    chunked.deps_overrides["on_unit"] = at_unit
    with monkeypatch.context() as m:
        m.setattr(HOLDER, "leave", lambda *_a, **_k: None)
        with pytest.raises(Stop):
            chunked.run_job(job_id)
    chunked.deps_overrides.pop("on_unit")

    # Before any chunk: nothing; after each committed chunk: that chunk's records, live.
    assert seen[0][0] == {}
    after_one = seen[CHUNK]
    assert after_one[0] == {"respond:generated": CHUNK, "pairs": 0}, after_one
    assert after_one[1] == pytest.approx(100.0 * CHUNK / N)
    assert after_one[2] == f"{CHUNK} of {N} prompts done."
    after_two = seen[2 * CHUNK]
    assert after_two[0] == {"respond:generated": 2 * CHUNK, "pairs": 0}, after_two

    # What the tile reads: the REST run, still running, with live counts and the job's progress.
    got = (await client.get(f"{RUNS}/{run_id}")).json()
    assert got["state"] == "running"
    assert got["counts"] == {"respond:generated": 2 * CHUNK, "pairs": 0}
    assert got["progress"] == {
        "job_id": job_id,
        "percent": pytest.approx(100.0 * 2 * CHUNK / N),
        "message": f"{2 * CHUNK} of {N} prompts done.",
    }
    listed = (await client.get(RUNS)).json()["items"]
    (row,) = [r for r in listed if r["id"] == run_id]
    assert row["counts"] == got["counts"] and row["progress"] == got["progress"]


async def test_a_finished_run_has_no_progress_and_its_final_counts(
    client: httpx.AsyncClient, chunked: Gen
) -> None:
    version = make_version(version_table([f"q {i:02d}" for i in range(N)]))
    template = await respond_template(client)
    response = await client.post(RUNS, json=standard_body(version, template, sample_size=N))
    run_id = str(response.json()["id"])
    assert chunked.run_until_done(run_id).state == "completed"
    got = (await client.get(f"{RUNS}/{run_id}")).json()
    assert got["progress"] is None
    assert got["counts"] == {"respond:generated": N, "pairs": 0}
