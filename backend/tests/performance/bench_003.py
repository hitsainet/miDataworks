"""Feature 003 benchmarks (FTASKS 14.3; FTDD 003 section 9.1). Run by hand, not collected:

    cd backend && DATA_DIR=/tmp/bench003 python -m tests.performance.bench_003

Each figure is measured on the path that implements it: a native preview through
``preview.run_preview`` (the task body) on a 300-row sample; a 45,000-row native step through
``executor.execute_in_process`` (batch loop, per-batch effects and conservation, staged writes);
the catalogue through the live route. The Data-Juicer preview is measured in the Data-Juicer
environment by ``datajuicer`` timing the runner's ``run_preview`` (see the controls record).
"""

from __future__ import annotations

import asyncio
import os
import statistics
import tempfile
import time
from pathlib import Path

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://midataworks:midataworks-dev@127.0.0.1:55433/midataworks_test_003",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:56380/13")
os.environ.setdefault("SETTINGS_ENCRYPTION_KEY", "bench-only-encryption-key-0123456789abcdef")
os.environ.setdefault("INTERNAL_API_SECRET", "bench-only-internal-secret-0123456789")
os.environ.setdefault("OPERATOR_TEST_FIXTURES", "true")
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="bench003-"))

from src.operators import executor, preview  # noqa: E402
from tests.support import operator_fixtures as fx  # noqa: E402


def texts(n: int) -> list[str]:
    words = "the quick brown fox jumps over a lazy dog while puns fly".split()
    return [" ".join(words[: 2 + (i * 7) % 11]) + f" #{i}" for i in range(n)]


def main() -> None:
    data_dir = Path(os.environ["DATA_DIR"])
    reg = fx.registry()
    results: dict[str, str] = {}

    files = sorted(fx.write_parts(data_dir / "v300", fx.table(texts(5000)), parts=4).glob("part-*"))
    request = {
        "operator": "fx_drop_short",
        "version": "1",
        "params": {"min_len": 25},
        "input": {},
        "sample_size": 300,
        "seed": 1,
        "mode": "preview",
        "resolved_input": {
            "files": [str(p.relative_to(data_dir)) for p in files],
            "column_roles": dict(fx.ROLES),
            "rowkey_scheme": "dw.rowkey/v1",
        },
    }
    timings = []
    for _ in range(20):
        start = time.perf_counter()
        preview.run_preview(reg, request)
        timings.append(time.perf_counter() - start)
    timings.sort()
    results["native preview 300 rows p95 (s)"] = f"{timings[int(0.95 * len(timings)) - 1]:.3f}"
    results["native preview 300 rows median (s)"] = f"{statistics.median(timings):.3f}"

    fx.write_parts(data_dir / "in45k", fx.table(texts(45_000)), parts=3)
    spec = fx.stage_spec(reg, "fx_drop_short", {"min_len": 25}, input_dir="in45k")
    start = time.perf_counter()
    result = executor.execute_in_process(spec, registry=reg)
    results["45,000-row native step (s)"] = (
        f"{time.perf_counter() - start:.2f} (dropped {result.rows_dropped})"
    )

    import httpx

    from src.main import fastapi_app
    from src.operators import registry as registry_module
    from src.services import operator_port

    registry_module._process = reg
    operator_port.install_registry(reg)

    async def catalogue() -> list[float]:
        transport = httpx.ASGITransport(app=fastapi_app)
        out = []
        async with httpx.AsyncClient(transport=transport, base_url="http://bench") as client:
            await client.get("/api/v1/operators")
            for _ in range(50):
                t = time.perf_counter()
                response = await client.get("/api/v1/operators")
                out.append(time.perf_counter() - t)
                assert response.status_code == 200
        return sorted(out)

    cat = asyncio.run(catalogue())
    results["catalogue route p95 (ms)"] = f"{1000 * cat[int(0.95 * len(cat)) - 1]:.1f}"
    for key, value in results.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
