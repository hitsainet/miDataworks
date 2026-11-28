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


# --- progress between chunk boundaries (2026-11-15) -------------------------------------------
# A carrier run of 250 prompts at about 5.5 s each read 0% for 18 minutes, then jumped to 80%: the
# job row (what the run tile, the Operations drawer and the janitor read) was written only when a
# 200-prompt chunk committed.


async def test_the_job_row_moves_between_chunk_boundaries(
    client: httpx.AsyncClient, gen: Gen, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.services.generation import run_engine

    monkeypatch.setattr(get_settings(), "generation_chunk_size", 100)  # one chunk: no boundary
    monkeypatch.setattr(get_settings(), "progress_heartbeat_seconds", 0.0)
    monkeypatch.setattr(run_engine, "EMIT_INTERVAL_S", 0.0)
    unforced: list[tuple[str, float, str | None]] = []
    real = run_engine.record_progress

    def spy(job_id: str, **kwargs: Any) -> bool:
        if not kwargs.get("force") and kwargs.get("status") is None:
            unforced.append((job_id, float(kwargs["progress"]), kwargs.get("message")))
        return bool(real(job_id, **kwargs))

    monkeypatch.setattr(run_engine, "record_progress", spy)
    version = make_version(version_table([f"q {i:02d}" for i in range(N)]))
    template = await respond_template(client)
    response = await client.post(RUNS, json=standard_body(version, template, sample_size=N))
    run_id = str(response.json()["id"])
    job_id = gen.job_for(run_id)
    seen: list[tuple[dict[str, Any], float, str | None]] = []
    gen.deps_overrides["on_unit"] = lambda _position: seen.append(snapshot(run_id, job_id))
    assert gen.run_until_done(run_id).state == "completed"
    gen.deps_overrides.pop("on_unit")

    # Before the fourth prompt, with no chunk committed: the row reads three of six.
    assert seen[3][1:] == (pytest.approx(50.0), f"3 of {N} prompts done.")
    # One throttled row write per prompt, each the prompts-done fraction.
    assert unforced == [
        (job_id, pytest.approx(100.0 * k / N), f"{k} of {N} prompts done.") for k in range(1, N + 1)
    ]
    # The Operations drawer follows the JOB's room: one event per prompt, percent only (it labels
    # rows_done as rows, and these are prompts).
    on_job = [d for room, e, d in gen.emitted if e == "job:progress"]
    assert {room for room, e, _ in gen.emitted if e == "job:progress"} == {
        f"dataworks/generation-runs/{job_id}"
    }
    assert on_job == [
        {"job_id": job_id, "progress": pytest.approx(100.0 * k / N)} for k in range(1, N + 1)
    ]


async def test_a_committed_chunk_is_written_past_the_throttle(
    client: httpx.AsyncClient, gen: Gen, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A per-prompt write inside the throttle window must not leave the boundary's exact figure
    unwritten: the boundary write is forced."""
    from src.services.generation import run_engine

    monkeypatch.setattr(get_settings(), "generation_chunk_size", CHUNK)
    monkeypatch.setattr(get_settings(), "progress_heartbeat_seconds", 3600.0)
    monkeypatch.setattr(run_engine, "EMIT_INTERVAL_S", 0.0)
    version = make_version(version_table([f"q {i:02d}" for i in range(N)]))
    template = await respond_template(client)
    response = await client.post(RUNS, json=standard_body(version, template, sample_size=N))
    run_id = str(response.json()["id"])
    job_id = gen.job_for(run_id)
    seen: list[tuple[dict[str, Any], float, str | None]] = []
    gen.deps_overrides["on_unit"] = lambda _position: seen.append(snapshot(run_id, job_id))
    assert gen.run_until_done(run_id).state == "completed"
    gen.deps_overrides.pop("on_unit")

    assert seen[CHUNK][1:] == (pytest.approx(100.0 * CHUNK / N), f"{CHUNK} of {N} prompts done.")
    assert seen[2 * CHUNK][1:] == (
        pytest.approx(100.0 * 2 * CHUNK / N),
        f"{2 * CHUNK} of {N} prompts done.",
    )
