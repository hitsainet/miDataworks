"""Compare and verify rebuild (tasks 11.1–11.3; FR-002.6, FR-002.38)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from src.core.database import sync_session_factory
from src.models import Job, VersionVerification
from tests.integration.test_version_build import build, request, setup, src
from tests.support import db_factories as f
from tests.support.stub_operators import body
from tests.support.version_fixtures import HUMOR_TRAIN, BuildDriver, driver, make_source

__all__ = ["driver"]
V = "/api/v1/versions"


@pytest.fixture
async def pair(
    client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
) -> dict[str, Any]:
    source = make_source(data_dir, {"train": HUMOR_TRAIN})
    ds, rev = await setup(client, body(("stub_keep", {})))
    v1 = await build(client, driver, ds, rev, [src(source)], seed=1)
    rec = await client.post(
        "/api/v1/recipes",
        json={
            "name": "trim",
            "body": body(("stub_drop_short", {"min_len": 8}), ("stub_upper", {})),
        },
    )
    v2 = await build(
        client,
        driver,
        ds,
        rec.json()["head_revision_id"],
        [{"kind": "version", "version_id": v1["id"]}],
        seed=1,
    )
    return {"v1": v1, "v2": v2, "dataset": ds, "revision": rev, "source": source}


async def test_compare_with_the_parent_reproduces_known_differences(
    client: httpx.AsyncClient, pair: dict[str, Any]
) -> None:
    report = (await client.get(f"{V}/{pair['v2']['id']}/compare")).json()
    assert report["version_b"]["id"] == pair["v1"]["id"] and report["cached"] is False
    assert report["splits"][0]["rows_a"] == 9 and report["splits"][0]["rows_b"] == 10
    assert report["splits"][0]["difference"] == -1
    distinct = len({r["text"].strip() for r in HUMOR_TRAIN})
    keys = report["keys"]
    assert keys["changed"] == distinct - 1  # every kept row was upper-cased
    assert keys["removed"] == 1  # "short" was dropped
    assert keys["added"] == 0 and keys["kept"] == 0
    drops = {(d["operator"], d["reason_code"]): d for d in report["drop_log"]}
    assert drops[("stub_drop_short", "too_short")]["a"] == 1
    assert drops[("stub_drop_short", "too_short")]["b"] == 0
    balance = report["splits"][0]["label_balance"]
    assert balance == {"label": {"a": {"0": 4, "1": 5}, "b": {"0": 5, "1": 5}}}  # "short" was a 0
    length = next(d for d in report["distributions"] if d["column"] == "text")
    assert length["kind"] == "length" and len(length["bins"]) == 31
    assert length["n_a"] == 9 and length["n_b"] == 10 and sum(length["a"]) == 9
    score = next(d for d in report["distributions"] if d["column"] == "score")
    assert score["kind"] == "numeric" and sum(score["b"]) == 10


async def test_a_repeat_comparison_comes_from_the_cache(
    client: httpx.AsyncClient, pair: dict[str, Any]
) -> None:
    first = (await client.get(f"{V}/{pair['v2']['id']}/compare")).json()
    second = (await client.get(f"{V}/{pair['v2']['id']}/compare?with={pair['v1']['id']}")).json()
    assert second["cached"] is True
    assert {k: v for k, v in second.items() if k != "cached"} == {
        k: v for k, v in first.items() if k != "cached"
    }


async def test_compare_refusals(client: httpx.AsyncClient, pair: dict[str, Any]) -> None:
    no_parent = await client.get(f"{V}/{pair['v1']['id']}/compare")
    assert no_parent.status_code == 409 and no_parent.json()["error"]["code"] == "no_parent"
    with sync_session_factory()() as db:
        other = f.version(db, rowkey_scheme="dw.rowkey/v2")
        db.commit()
        other_id = other.id
    mismatch = await client.get(f"{V}/{pair['v2']['id']}/compare?with={other_id}")
    assert (
        mismatch.status_code == 409 and mismatch.json()["error"]["code"] == "rowkey_scheme_mismatch"
    )
    await client.request("DELETE", f"{V}/{pair['v2']['id']}", json={"reason": "test"})
    deleted = await client.get(f"{V}/{pair['v2']['id']}/compare")
    assert deleted.status_code == 409 and deleted.json()["error"]["code"] == "version_deleted"


async def test_verify_rebuild_matches_a_deterministic_version(
    client: httpx.AsyncClient, driver: BuildDriver, pair: dict[str, Any]
) -> None:
    executions = driver.stubs.executions
    response = await client.post(f"{V}/{pair['v2']['id']}/verify-rebuild")
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    assert driver.run(job_id) == "completed"
    assert driver.stubs.executions > executions, "verify recomputes; reuse is disabled"
    with sync_session_factory()() as db:
        row = db.execute(select(VersionVerification)).scalar_one()
        assert row.result == "match" and row.first_mismatch is None
        assert all(s["expected"] == s["actual"] for s in row.splits)
        job = db.get(Job, job_id)
        assert job is not None and job.kind == "version_verify"


async def test_verify_rebuild_reports_the_first_difference_of_an_unseeded_operator(
    client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
) -> None:
    source = make_source(data_dir, {"train": HUMOR_TRAIN * 4})
    ds, rev = await setup(client, body(("stub_unseeded", {})))
    version = await build(client, driver, ds, rev, [src(source)], seed=1)
    job_id = (await client.post(f"{V}/{version['id']}/verify-rebuild")).json()["job_id"]
    assert driver.run(job_id) == "completed"
    with sync_session_factory()() as db:
        row = db.execute(select(VersionVerification)).scalar_one()
    assert row.result == "mismatch"
    assert row.first_mismatch is not None and row.first_mismatch["split"] == "train"
    assert row.first_mismatch["expected"] != row.first_mismatch["actual"]


async def test_verify_of_a_deleted_version_is_refused(
    client: httpx.AsyncClient, pair: dict[str, Any]
) -> None:
    await client.request("DELETE", f"{V}/{pair['v2']['id']}", json={"reason": "gone"})
    response = await client.post(f"{V}/{pair['v2']['id']}/verify-rebuild")
    assert response.status_code == 409 and response.json()["error"]["code"] == "version_deleted"


async def test_a_build_started_after_verify_is_not_mistaken_for_it(
    client: httpx.AsyncClient, driver: BuildDriver, pair: dict[str, Any]
) -> None:
    """The verify job shares the version's request digest; dedupe must ignore it."""
    await client.post(f"{V}/{pair['v1']['id']}/verify-rebuild")
    again = await request(client, pair["dataset"], pair["revision"], [src(pair["source"])], seed=1)
    assert again.status_code == 200 and again.json()["id"] == pair["v1"]["id"]
