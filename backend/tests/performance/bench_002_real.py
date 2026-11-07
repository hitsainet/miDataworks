"""002 FTASKS 21.2 (suite tracker 4.9): 18.7's lineage overhead re-measured against feature 003's
REAL executor. Run by hand (not collected; name it explicitly):

    cd backend && pytest tests/performance/bench_002_real.py -s -p no:cacheprovider

The 45,000-row source of 18.7 is built through the real orchestrator and ingestion, with each step
run by 003's real task body (``operator_tasks.step_curation``) through ``RealDriver``. "Steps" is the
time inside those task bodies (dispatch payload in, parts/events/meta out); "bookkeeping" is the
rest of the build: assembly, dispatch, event ingestion, finalisation, the version row. Two steps:
``fx_drop_short`` (drops rows with reasons) and ``fx_mapper_upper`` (changes every row, so every
row produces a ``changed`` event the ingestion must store) - the heaviest lineage shape.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx

from tests.integration.test_version_build import request, setup, src
from tests.performance.bench_002 import _rows
from tests.support.operator_build_driver import RealDriver, real_driver
from tests.support.stub_operators import body
from tests.support.version_fixtures import make_source

__all__ = ["real_driver"]


async def test_lineage_overhead_on_45000_rows_with_the_real_executor(
    client: httpx.AsyncClient, real_driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    source = make_source(data_dir, {"train": _rows(45_000)})
    ds, rev = await setup(client, body(("fx_drop_short", {"min_len": 41}), ("fx_mapper_upper", {})))
    steps = 0.0
    original = real_driver.run_step

    def timed() -> list[Any] | None:
        nonlocal steps
        t0 = time.perf_counter()
        try:
            return original()
        finally:
            steps += time.perf_counter() - t0

    real_driver.run_step = timed  # type: ignore[method-assign]
    started = time.perf_counter()
    job_id = (await request(client, ds, rev, [src(source)], seed=1)).json()["job_id"]
    status = real_driver.run(job_id)
    if status != "completed":
        from src.core.database import sync_session_factory
        from src.models.job import Job

        with sync_session_factory()() as db:
            job = db.get(Job, job_id)
            print("\nFAILED:", job.error if job else None, real_driver.step_results[-1:])
    assert status == "completed"
    total = time.perf_counter() - started
    bookkeeping = total - steps
    print(
        f"\n45,000 rows, real executor: build {total:.2f} s, steps {steps:.2f} s, "
        f"bookkeeping {bookkeeping:.2f} s, overhead {bookkeeping / steps:.0%} of step time"
    )
