"""The contract with feature 003's executor (task 8.8; C-002.10, FR-002.47).

Run against the conforming stub executor now; re-run against 003's real executor when it lands
(swap the registry fixture). It pins: the payload 002 sends, the output layout, the link callback,
and that conservation is counted in (row key, occurrence) pairs.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pyarrow.parquet as pq

from src.services.step_contract import EVENTS_FILE, META_FILE, part_files, read_meta
from tests.integration.test_version_build import request, setup, src
from tests.support.stub_operators import body
from tests.support.version_fixtures import HUMOR_TRAIN, BuildDriver, driver, make_source

__all__ = ["driver"]

PAYLOAD_FIELDS = {
    "step_execution_id",
    "operator",
    "version",
    "params",
    "input_dir",
    "output_dir",
    "step_seed",
    "job_id",
    "bindings",
    "column_roles",
    "rowkey_scheme",
    "expected_manifest_hash",
}


async def test_payload_layout_link_and_pair_conservation(
    client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
) -> None:
    from src.workers import version_build_tasks

    source = make_source(data_dir, {"train": HUMOR_TRAIN})
    ds, rev = await setup(client, body(("stub_drop_short", {"min_len": 8})))
    job_id = (await request(client, ds, rev, [src(source)], seed=1)).json()["job_id"]
    version_build_tasks.run_pass(job_id)
    assert len(driver.stubs.pending) == 1
    spec, link_task, link_args = driver.stubs.pending[0]
    assert set(spec.as_payload()) == PAYLOAD_FIELDS
    assert link_task == "midataworks.versions.advance_build" and link_args == [job_id]
    assert spec.output_dir.startswith(f"runs/{job_id}/steps/")
    assert spec.column_roles["text"] == "content"
    assert 0 <= spec.step_seed < 2**32
    driver.stubs.run_pending()
    out = data_dir / spec.output_dir
    assert part_files(out) and (out / EVENTS_FILE).is_file() and (out / META_FILE).is_file()
    meta = read_meta(out)
    assert meta["rows_in"] == len(HUMOR_TRAIN)
    pairs = [
        (r["_dw_row_key"], r["_dw_occurrence"])
        for r in pq.read_table(part_files(out)[0]).to_pylist()
    ]
    assert len(pairs) == len(set(pairs)), "the output names each (row key, occurrence) once"
    keys = [k for k, _ in pairs]
    assert len(keys) > len(set(keys)), "duplicate keys survive: conservation is by pair"
    assert driver.run(job_id) == "completed"
