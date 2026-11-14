"""Feature 002 benchmarks (task 18.7). Run by hand, not collected by pytest (not test_*.py):

    cd backend && DATABASE_URL=...midataworks_scratch_test python -m tests.performance.bench_002

Each figure is measured on the code path that implements it (global rule: benchmark the changed
path): the 45,000-row lineage overhead through the real orchestrator and ingestion; row history
through lineage_service.row_history against 10,000,000 events; a row page through
lineage_service.rows_page on a 1,000,000-row split; version list and detail through the routes.
"""

from __future__ import annotations

import time
from pathlib import Path

import httpx
from sqlalchemy import text

from src.core.database import get_sync_engine
from tests.integration.test_version_build import build, setup, src
from tests.support.stub_operators import body
from tests.support.version_fixtures import BuildDriver, driver, make_source

__all__ = ["driver"]

RESULTS: dict[str, float] = {}


def _rows(n: int) -> list[dict[str, object]]:
    return [
        {
            "text": f"benchmark row {i} with some ordinary words",
            "label": i % 2,
            "score": (i % 100) / 100,
            "note": None,
        }
        for i in range(n)
    ]


async def test_lineage_overhead_on_45000_rows(
    client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
) -> None:
    """Build with bookkeeping vs the same steps run through the executor alone."""
    source = make_source(data_dir, {"train": _rows(45_000)})
    recipe = body(("stub_band", {"min": 0.3, "max": 0.7}), ("stub_balance", {"column": "label"}))
    ds, rev = await setup(client, recipe)
    started = time.perf_counter()
    await build(client, driver, ds, rev, [src(source)], seed=1)
    total = time.perf_counter() - started
    executor = 0.0
    for spec in list(driver.stubs.dispatched):
        t0 = time.perf_counter()
        driver.stubs.execute(spec)
        executor += time.perf_counter() - t0
    RESULTS["build_45k_s"] = total
    RESULTS["executor_alone_s"] = executor
    print(
        f"\n45,000 rows: build {total:.2f} s, executor alone {executor:.2f} s, bookkeeping {total - executor:.2f} s"
    )


async def test_row_page_on_a_1m_row_split(
    client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
) -> None:
    from src.core.database import async_session_factory
    from src.services import lineage_service

    source = make_source(data_dir, {"train": _rows(1_000_000)})
    ds, rev = await setup(client, body(("stub_keep", {})))
    t0 = time.perf_counter()
    version = await build(client, driver, ds, rev, [src(source)], seed=1)
    print(f"\n1,000,000-row build through stubs: {time.perf_counter() - t0:.1f} s")
    async with async_session_factory()() as db:
        timings = []
        for page in (1, 500, 19_999):
            t0 = time.perf_counter()
            result = await lineage_service.rows_page(
                db, version["id"], split="train", query=None, where=None, page=page, limit=50
            )
            timings.append(time.perf_counter() - t0)
            assert len(result["items"]) == 50
        t0 = time.perf_counter()
        await client.get("/api/v1/versions", params={"dataset_id": ds})
        await client.get(f"/api/v1/versions/{version['id']}")
        listing = time.perf_counter() - t0
    RESULTS["row_page_worst_s"] = max(timings)
    print(
        f"row page (50 rows) on 1,000,000 rows: {[round(t, 3) for t in timings]} s; list+detail {listing:.3f} s"
    )
    assert max(timings) <= 1.0 and listing <= 1.0


async def test_row_history_at_10m_events(
    client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
) -> None:
    from src.core.database import async_session_factory
    from src.services import lineage_service
    from src.services.row_keys import compute_row_key

    source = make_source(data_dir, {"train": _rows(2_000)})
    ds, rev = await setup(client, body(("stub_drop_short", {"min_len": 100})))  # drops every row
    version = await build(client, driver, ds, rev, [src(source)], seed=1)
    # Fill 10,000,000 unrelated events across 2,000 other step executions (all partitions).
    with get_sync_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO dw_row_events (step_execution_id, seq, kind, row_key, occurrence, reason_code, reason, statistic_name, statistic_value) "
                "SELECT md5(e::text)::uuid, s, 'dropped', sha256((e * 10000 + s)::text::bytea), 0, 'filler', 'filler', 'n', 1 "
                "FROM generate_series(1, 2000) e, generate_series(1, 5000) s"
            )
        )
        conn.execute(text("ANALYZE dw_row_events"))
        total = conn.execute(text("SELECT count(*) FROM dw_row_events")).scalar_one()
    key = compute_row_key({"text": "benchmark row 77 with some ordinary words"}, ["text"])
    async with async_session_factory()() as db:
        row = await lineage_service.get_version_row(db, version["id"])
        t0 = time.perf_counter()
        result = await lineage_service.row_history(db, row, key)
        elapsed = time.perf_counter() - t0
    RESULTS["row_history_s"] = elapsed
    print(f"\nrow history at {total:,} events: {elapsed:.3f} s ({result['status']})")
    assert result["status"] == "dropped" and elapsed <= 2.0
