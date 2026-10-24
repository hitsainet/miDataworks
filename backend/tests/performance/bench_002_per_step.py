"""002 FTASKS 21.2 (round 2, 2026-10-17): lineage bookkeeping in ABSOLUTE time per step, on 004's
real operators through 003's real executor. Run by hand (not collected; name it explicitly):

    cd backend && pytest tests/performance/bench_002_per_step.py -s -p no:cacheprovider

The 45,000-row source of 18.7 is built with K = 1, 2, 4 and 8 steps of real, fast operators
(``metadata_value_filter`` steps, each dropping a different 1/10 of the remaining rows, so every
step writes drop events the ingestion must store). For each K: "steps" is the time inside 003's
step task bodies; "bookkeeping" is the rest of the build (assembly, dispatch, event ingestion,
finalisation, the version row). A least-squares line through bookkeeping(K) splits it into a fixed
per-build cost (intercept) and a marginal cost per step (slope) — the figure the target is about.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from src.operators import executor
from src.operators import registry as registry_module
from src.services import operator_port
from tests.integration.test_version_build import request, setup, src
from tests.support.curation_fixtures import registry
from tests.support.operator_build_driver import RealDriver
from tests.support.version_fixtures import make_source

ROWS = 45_000
KS = (1, 2, 4, 8)
REPEATS = 3


def _rows(n: int, tag: str = "") -> list[dict[str, object]]:
    # ``tag`` makes every measured build's source distinct, so no step is reused from an earlier
    # build (002's step reuse would otherwise make every later step cost 0 ms).
    return [
        {"text": f"benchmark row {i} {tag} with some ordinary words", "bucket": f"b{i % 10}"}
        for i in range(n)
    ]


@pytest.fixture
def curation_driver(monkeypatch: pytest.MonkeyPatch, data_dir: Path) -> Iterator[RealDriver]:
    from src.core.celery_app import celery_app
    from src.workers import version_build_tasks

    reg = registry()
    monkeypatch.setattr(operator_port, "_registry", reg)
    monkeypatch.setattr(registry_module, "_process", reg)
    drv = RealDriver(reg)
    monkeypatch.setattr(executor, "send_task", drv.record_step)
    monkeypatch.setattr(celery_app, "send_task", drv.send_task)
    monkeypatch.setattr(version_build_tasks, "_send_task", drv.send_task)
    monkeypatch.setattr(version_build_tasks, "emit", drv.emit)
    yield drv


def _body(k: int) -> dict[str, Any]:
    return {
        "format": "dw.recipe/v1",
        "steps": [
            {
                "operator": "metadata_value_filter",
                "version": "1.0.0",
                "params": {"column": "bucket", "values": [f"b{i}"]},
            }
            for i in range(k)
        ],
    }


async def _one(
    client: httpx.AsyncClient, drv: RealDriver, source: str, k: int, name: str
) -> tuple[float, float]:
    ds, rev = await setup(client, _body(k), name=name)
    steps = 0.0
    original = RealDriver.run_step

    def timed(self: RealDriver) -> list[Any] | None:
        nonlocal steps
        t0 = time.perf_counter()
        try:
            return original(self)
        finally:
            steps += time.perf_counter() - t0

    drv.run_step = timed.__get__(drv)  # type: ignore[method-assign]
    started = time.perf_counter()
    job_id = (await request(client, ds, rev, [src(source)], seed=1)).json()["job_id"]
    assert drv.run(job_id) == "completed", drv.step_results[-1:]
    total = time.perf_counter() - started
    return total - steps, steps


async def test_profile_one_eight_step_build(
    client: httpx.AsyncClient, curation_driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    """``BENCH_PROFILE=1``: cProfile of one 8-step build, top functions by own time."""
    import cProfile
    import os
    import pstats

    if not os.environ.get("BENCH_PROFILE"):
        pytest.skip("set BENCH_PROFILE=1")
    warm = make_source(data_dir, {"train": _rows(ROWS, "pwarm")})
    await _one(client, curation_driver, warm, 1, "pwarm")
    source = make_source(data_dir, {"train": _rows(ROWS, "prof")})
    profiler = cProfile.Profile()
    profiler.enable()
    await _one(client, curation_driver, source, 8, "prof")
    profiler.disable()
    pstats.Stats(profiler).sort_stats("tottime").print_stats(30)


async def test_bookkeeping_per_step_in_milliseconds(
    client: httpx.AsyncClient, curation_driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    import os

    warm = make_source(data_dir, {"train": _rows(ROWS, "warmup")})
    await _one(client, curation_driver, warm, 1, "warmup")  # imports, caches, first connection
    points: list[tuple[int, float, float]] = []
    for r in range(REPEATS):
        for k in KS:
            source = make_source(data_dir, {"train": _rows(ROWS, f"k{k}r{r}")})
            book, steps = await _one(client, curation_driver, source, k, f"k{k}r{r}")
            assert steps > 0, "a step was reused; the measurement would be meaningless"
            points.append((k, book, steps))
            print(f"K={k} run {r}: bookkeeping {book * 1000:.0f} ms, steps {steps * 1000:.0f} ms")
    n = len(points)
    mean_k = sum(p[0] for p in points) / n
    mean_b = sum(p[1] for p in points) / n
    slope = sum((p[0] - mean_k) * (p[1] - mean_b) for p in points) / sum(
        (p[0] - mean_k) ** 2 for p in points
    )
    intercept = mean_b - slope * mean_k
    step_ms = sum(p[2] for p in points) / sum(p[0] for p in points) * 1000
    print(
        f"\n{ROWS:,} rows, load {os.getloadavg()[0]:.1f}: bookkeeping = {intercept * 1000:.0f} ms "
        f"per build + {slope * 1000:.0f} ms per step; a step itself takes {step_ms:.0f} ms; "
        f"marginal overhead {slope * 1000 / step_ms:.0%} of step time"
    )
