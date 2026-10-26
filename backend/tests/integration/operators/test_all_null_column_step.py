"""A source column that is null on every row must not fail a step (found 2026-10-16 while
re-measuring 002 FTASKS 21.2 against the real executor).

Parquet stores an all-null column with Arrow's ``null`` type. DuckDB reads it back as ``INTEGER``,
and ``executor._conform`` then tried ``int32 -> null``, which Arrow cannot cast, so EVERY real step
over such a source failed with ``ArrowNotImplementedError``. Hugging Face splits with a column that
is empty in one split (a ``note``, an unused ``meanGrade``) have this shape.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pyarrow as pa

from src.operators.executor import _conform
from tests.integration.test_version_build import request, setup, src
from tests.support.operator_build_driver import RealDriver, real_driver
from tests.support.stub_operators import body
from tests.support.version_fixtures import make_source

__all__ = ["real_driver"]


def test_conform_keeps_an_all_null_column_null() -> None:
    schema = pa.schema([("text", pa.string()), ("note", pa.null())])
    read_back = pa.table({"text": ["a", "b"], "note": pa.array([None, None], pa.int32())})
    out = _conform(read_back, schema)
    assert out.schema == schema and out.column("note").null_count == 2


async def test_a_real_step_runs_over_a_source_with_an_all_null_column(
    client: httpx.AsyncClient, real_driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    rows = [{"text": f"a row of text number {i}", "note": None} for i in range(30)]
    source = make_source(data_dir, {"train": rows})
    ds, rev = await setup(client, body(("fx_drop_short", {"min_len": 5})))
    job_id = (await request(client, ds, rev, [src(source)], seed=1)).json()["job_id"]
    assert real_driver.run(job_id) == "completed", real_driver.step_results
