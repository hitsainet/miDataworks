"""Feature 006 benchmarks (FTASKS 14.4, 14.5; FTDD 006 section 9). Run by hand, not collected:

    cd backend && DATABASE_URL=postgresql+asyncpg://…/midataworks_test_006_gen python -m tests.performance.bench_006

The database must be migrated to head and is EMPTIED. Measured on the changed paths:
``record_service.compute`` (the worker's body, through PostgreSQL) on a 10,000-row set with five
ratings per row; the ceiling alone on 15,000 rated rows; an item page of 50 through the live route;
``effective_label.resolve`` over 100,000 row keys with decisions on some of them.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
from pathlib import Path

os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:56380/3")
os.environ.setdefault("SETTINGS_ENCRYPTION_KEY", "bench-only-encryption-key-0123456789abcdef")
os.environ.setdefault("INTERNAL_API_SECRET", "bench-only-internal-secret-0123456789")
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="bench006-"))
assert "test" in os.environ["DATABASE_URL"], "benchmarks run against a test database only"

import numpy as np  # noqa: E402
from sqlalchemy import text  # noqa: E402

from src.core.database import Base, sync_session_factory  # noqa: E402


def empty() -> None:
    with sync_session_factory()() as s:
        s.execute(
            text("TRUNCATE " + ", ".join(t.name for t in Base.metadata.sorted_tables) + " CASCADE")
        )
        s.execute(
            text(
                "INSERT INTO dw_app_settings (key, value, is_sensitive, category) "
                "VALUES ('operator_name', 'Bench', false, 'identity')"
            )
        )
        s.commit()


async def main() -> None:
    import httpx

    from src.main import fastapi_app
    from src.models.job import Job
    from src.services.calibration import ceiling, record_service
    from src.services.review import effective_label
    from tests.support.calibration_fixtures import (
        LABELS,
        MAPPING,
        QUESTION,
        humor_rows,
        humor_scores,
        make_run,
        make_version,
        row_key,
    )

    data = Path(os.environ["DATA_DIR"])
    empty()
    transport = httpx.ASGITransport(app=fastapi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://b") as http:
        rows = humor_rows(13_336)  # 10,002 edited rows with five ratings, 3,334 originals
        version = make_version(data, rows)
        run = make_run(version, humor_scores(rows))
        set_id = (
            await http.post(
                "/api/v1/calibration-sets/import",
                json={
                    "version_id": version,
                    "question": QUESTION,
                    "label_set": LABELS,
                    "mapping": MAPPING,
                },
            )
        ).json()["id"]
        record_service._dispatch = lambda: []  # type: ignore[assignment]
        job_id = (
            await http.post(
                "/api/v1/calibration-records",
                json={"label_run_id": run.id, "calibration_set_id": set_id},
            )
        ).json()["job_id"]
        with sync_session_factory()() as s:
            job = s.get(Job, job_id)
            assert job is not None
            started = time.perf_counter()
            record_service.compute(s, job)
            print(
                f"14.4 record, 13,336 set rows (10,002 rated): {time.perf_counter() - started:.1f} s"
            )

        rng = np.random.default_rng(0)
        R, counts = ceiling.ratings_matrix([rng.integers(0, 4, 5).tolist() for _ in range(15_000)])
        started = time.perf_counter()
        draws = ceiling.held_out_draws(
            R, counts, positive_at_or_above=1.6, negative_at_or_below=0.4
        )
        ceiling.ceiling_and_comparison(draws, rng.random(15_000), n_rows=15_000)
        print(f"14.4 ceiling, 15,000 per-rater rows: {time.perf_counter() - started:.1f} s")

        queue = (
            await http.post(
                "/api/v1/review-queues",
                json={"kind": "label_review", "label_run_id": run.id, "size": 500},
            )
        ).json()
        times = []
        for _ in range(5):
            started = time.perf_counter()
            r = await http.get(f"/api/v1/review-queues/{queue['id']}/items?limit=50")
            assert r.status_code == 200
            times.append(time.perf_counter() - started)
        print(f"14.5 item page of 50: median {sorted(times)[2]:.3f} s")

        page = (await http.get(f"/api/v1/review-queues/{queue['id']}/items?limit=200")).json()[
            "items"
        ]
        for item in page[:200]:
            await http.post(
                f"/api/v1/review-items/{item['id']}/decisions", json={"decision": "accept"}
            )
        keys = [row_key(r["text"]) for r in rows] + [
            f"{i:064x}" for i in range(100_000 - len(rows))
        ]
        started = time.perf_counter()
        resolved = effective_label.resolve(version, None, run.id, keys)
        print(f"14.5 resolver, {len(resolved):,} rows: {time.perf_counter() - started:.2f} s")


if __name__ == "__main__":
    asyncio.run(main())
