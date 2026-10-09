"""Delete as tombstone (tasks 12.1–12.4; FR-002.37, P-15, C6)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

import src.main  # noqa: F401 - the app imports every feature's delete checkers, as production does
from src.core.database import Base, sync_session_factory
from src.models import Approval, RowEvent, Version
from src.services.version_delete_service import (
    OWN_TABLES,
    REFERENCE_CHECKERS,
    Reference,
    ReferenceChecker,
)
from src.workers import version_build_tasks
from tests.integration.test_version_build import build, request, setup, src
from tests.support.stub_operators import body
from tests.support.version_fixtures import HUMOR_TRAIN, BuildDriver, driver, make_source

__all__ = ["driver"]
V = "/api/v1/versions"
AGENT = {"X-Dataworks-Agent": "agent:mcp"}


@pytest.fixture
async def built(
    client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
) -> dict[str, Any]:
    source = make_source(data_dir, {"train": HUMOR_TRAIN})
    ds, rev = await setup(client, body(("stub_drop_short", {"min_len": 8})))
    v1 = await build(client, driver, ds, rev, [src(source)], seed=1)
    return {"v1": v1, "ds": ds, "rev": rev, "source": source}


async def delete(client: httpx.AsyncClient, version_id: str, **kw: Any) -> httpx.Response:
    return await client.request("DELETE", f"{V}/{version_id}", json={"reason": "superseded"}, **kw)


async def test_delete_tombstones_keeps_the_record_and_removes_files(
    client: httpx.AsyncClient, built: dict[str, Any], data_dir: Path, operator_name: str
) -> None:
    v1 = built["v1"]
    response = await delete(client, v1["id"])
    assert response.status_code == 200, response.text
    out = response.json()
    assert (
        out["state"] == "deleted"
        and out["deleted_by"] == operator_name
        and out["delete_reason"] == "superseded"
    )
    assert not (data_dir / "versions" / v1["id"]).exists()
    manifest = await client.get(f"{V}/{v1['id']}/manifest")
    assert manifest.status_code == 200 and manifest.headers["etag"] == v1["manifest_sha256"]
    assert (await client.get(f"{V}/{v1['id']}/drop-log")).json()["steps"][0]["dropped"] == 1
    rows = await client.get(f"{V}/{v1['id']}/rows")
    assert rows.status_code == 409 and rows.json()["error"]["code"] == "version_deleted"
    from src.services.row_keys import compute_row_key

    history = (
        await client.get(
            f"{V}/{v1['id']}/rows/history",
            params={"row_key": compute_row_key({"text": "short"}, ["text"])},
        )
    ).json()
    assert history["results"][0]["status"] == "dropped"
    with sync_session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(RowEvent)) > 0


async def test_a_number_is_never_reused(
    client: httpx.AsyncClient, driver: BuildDriver, built: dict[str, Any]
) -> None:
    await delete(client, built["v1"]["id"])
    v2 = await build(client, driver, built["ds"], built["rev"], [src(built["source"])], seed=1)
    assert v2["number"] == 2 and v2["id"] != built["v1"]["id"]


async def test_a_parent_may_be_deleted_and_its_child_keeps_its_rows(
    client: httpx.AsyncClient, driver: BuildDriver, built: dict[str, Any], data_dir: Path
) -> None:
    child = await build(
        client,
        driver,
        built["ds"],
        built["rev"],
        [{"kind": "version", "version_id": built["v1"]["id"]}],
        seed=1,
    )
    assert (await delete(client, built["v1"]["id"])).status_code == 200
    rows = await client.get(f"{V}/{child['id']}/rows")
    assert rows.status_code == 200 and rows.json()["total"] == child["total_rows"]


async def test_delete_is_refused_while_a_job_reads_the_version(
    client: httpx.AsyncClient, driver: BuildDriver, built: dict[str, Any]
) -> None:
    response = await request(
        client,
        built["ds"],
        built["rev"],
        [{"kind": "version", "version_id": built["v1"]["id"]}],
        seed=2,
    )
    job_id = response.json()["job_id"]
    version_build_tasks.run_pass(job_id)  # running, waiting on its step
    refused = await delete(client, built["v1"]["id"])
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "version_in_use"
    assert job_id in refused.json()["error"]["details"]["jobs"]


@pytest.mark.parametrize(
    ("code", "table"),
    [("version_published", "dw_publishes"), ("version_in_detector_set", "dw_detector_set_roles")],
)
async def test_reference_checkers_refuse(
    client: httpx.AsyncClient,
    built: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    code: str,
    table: str,
) -> None:
    async def check(db: Any, version_id: str) -> Reference | None:
        return Reference(code, f"refused by {table}", {"version_id": version_id})

    monkeypatch.setitem(REFERENCE_CHECKERS, table, ReferenceChecker("test", table, check))
    refused = await delete(client, built["v1"]["id"])
    assert refused.status_code == 409 and refused.json()["error"]["code"] == code


def test_every_table_referencing_versions_has_a_checker() -> None:
    """No hand-kept list: any table outside 002 with a foreign key to dw_versions must be covered."""
    referencing = {
        table.name
        for table in Base.metadata.sorted_tables
        for fk in table.foreign_keys
        if fk.column.table.name == "dw_versions"
    }
    assert "dw_version_inputs" in referencing  # the scan sees real foreign keys
    missing = sorted(referencing - OWN_TABLES - set(REFERENCE_CHECKERS))
    assert not missing, f"tables referencing dw_versions with no delete checker: {missing}"


class TestAgentDelete:
    async def test_an_agent_delete_waits_for_approval_and_runs_once(
        self, client: httpx.AsyncClient, built: dict[str, Any]
    ) -> None:
        v1 = built["v1"]["id"]
        response = await delete(client, v1, headers=AGENT)
        assert response.status_code == 202 and response.json()["action"] == "version_delete"
        assert (await client.get(f"{V}/{v1}")).json()["state"] == "completed"
        with sync_session_factory()() as db:
            approval = db.execute(select(Approval)).scalar_one()
            assert approval.payload == {"version_id": v1, "body": {"reason": "superseded"}}
        approved = await client.post(f"/api/v1/approvals/{response.json()['approval_id']}/approve")
        assert approved.json()["status"] == "executed"
        got = (await client.get(f"{V}/{v1}")).json()
        assert got["state"] == "deleted" and got["deleted_by"] == "agent:mcp"
        again = await client.post(f"/api/v1/approvals/{response.json()['approval_id']}/approve")
        assert again.status_code == 409

    async def test_the_digest_covers_the_reason(
        self, client: httpx.AsyncClient, built: dict[str, Any]
    ) -> None:
        a = await client.request(
            "DELETE", f"{V}/{built['v1']['id']}", json={"reason": "one"}, headers=AGENT
        )
        b = await client.request(
            "DELETE", f"{V}/{built['v1']['id']}", json={"reason": "two"}, headers=AGENT
        )
        assert a.json()["request_digest"] != b.json()["request_digest"]

    async def test_an_operator_delete_runs_directly(
        self, client: httpx.AsyncClient, built: dict[str, Any]
    ) -> None:
        assert (await delete(client, built["v1"]["id"])).status_code == 200
        with sync_session_factory()() as db:
            assert db.scalar(select(func.count()).select_from(Approval)) == 0
            assert db.get(Version, built["v1"]["id"]).state == "deleted"  # type: ignore[union-attr]
