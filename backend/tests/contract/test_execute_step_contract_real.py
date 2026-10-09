"""002's executor contract re-run against feature 003's REAL executor (FTASKS 12.3; C-002.10).

The stub-based ``test_execute_step_contract.py`` pins the contract from 002's side; this pins that
003's ``dispatch_step`` sends that payload with 002's link, and that ``execute_in_process`` writes the
layout 002 ingests, counted in (row key, occurrence) pairs.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pyarrow.parquet as pq

from src.services.step_contract import EVENTS_FILE, META_FILE, part_files, read_meta
from tests.contract.test_execute_step_contract import PAYLOAD_FIELDS
from tests.integration.test_version_build import request, setup, src
from tests.support.operator_build_driver import ADVANCE, RealDriver, real_driver
from tests.support.stub_operators import body
from tests.support.version_fixtures import HUMOR_TRAIN, make_source

__all__ = ["real_driver"]


async def test_payload_link_layout_and_pair_conservation(
    client: httpx.AsyncClient, real_driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    from src.workers import version_build_tasks

    source = make_source(data_dir, {"train": HUMOR_TRAIN})
    ds, rev = await setup(client, body(("fx_drop_short", {"min_len": 8})))
    job_id = (await request(client, ds, rev, [src(source)], seed=1)).json()["job_id"]
    version_build_tasks.run_pass(job_id)
    assert len(real_driver.steps) == 1, "dispatch_step sent exactly one task"
    name, options = real_driver.steps[0]
    assert name == "midataworks.operators.step.curation"
    payload = options["args"][0]
    assert set(payload) == PAYLOAD_FIELDS
    assert options["link"]["task"] == ADVANCE and list(options["link"]["args"]) == [job_id]
    assert payload["output_dir"].startswith(f"runs/{job_id}/steps/")
    real_driver.run_step()
    out = data_dir / payload["output_dir"]
    assert part_files(out) and (out / EVENTS_FILE).is_file() and (out / META_FILE).is_file()
    meta = read_meta(out)
    assert meta["rows_in"] == len(HUMOR_TRAIN) and meta["rows_dropped"] == 1
    rows = pq.read_table(part_files(out)[0]).to_pylist()
    pairs = [(r["_dw_row_key"], r["_dw_occurrence"]) for r in rows]
    assert len(pairs) == len(set(pairs))
    keys = [k for k, _ in pairs]
    assert len(keys) > len(set(keys)), "duplicate keys survive: conservation is by pair"
    assert real_driver.run(job_id) == "completed"
