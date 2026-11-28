"""``sample`` inside a real build: 003's executor, 002's finalize, the version's drop summary and its
recorded seed (novice walk 2026-10-19, finding 4: "a version of 25,000 rows from a 200,000-row
source"). Same-seed determinism is pinned in ``tests/unit/curation/test_sample_operator.py``."""

from __future__ import annotations

from pathlib import Path

import httpx

from src.core.database import sync_session_factory
from src.models.version import Version
from tests.integration.curation import test_split_build as split_build
from tests.integration.test_version_build import request, setup, src
from tests.support.operator_build_driver import RealDriver
from tests.support.version_fixtures import make_source

_body, _rows = split_build._body, split_build._rows
driver = split_build.driver


async def _versions(ds: str) -> list[Version]:
    with sync_session_factory()() as db:
        rows = db.query(Version).filter(Version.dataset_id == ds).order_by(Version.number).all()
        for row in rows:
            db.expunge(row)
    return rows


async def test_a_sampled_version_has_the_count_its_drop_log_and_its_seed(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    source = make_source(data_dir, {"train": _rows(400)})
    ds, rev = await setup(client, _body(("sample", {"mode": "count", "count": 125})))
    first = await request(client, ds, rev, [src(source)], seed=11)
    assert first.status_code == 202, first.text
    assert driver.run(first.json()["job_id"]) == "completed", driver.step_results
    (version,) = await _versions(ds)
    assert version.total_rows == 125
    detail = (await client.get(f"/api/v1/versions/{version.id}")).json()
    step = next(s for s in detail["drop_summary"] if s["operator"] == "sample")
    assert (step["rows_in"], step["rows_out"], step["dropped"]) == (400, 125, 275)
    assert [r["reason_code"] for r in step["reasons"]] == ["not_sampled"]
    assert detail["seed"] == 11
    page = (await client.get(f"/api/v1/versions/{version.id}/rows", params={"limit": 200})).json()
    assert page["total"] == 125
