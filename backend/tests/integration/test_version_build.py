"""Build requests and full builds with the real orchestrator and stub operators (tasks 6.x–9.x).

Every build here runs ``advance_build``'s real task body against the real test database and a
private data volume; only feature 003's executor is the stub (FR-003.3).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pyarrow.parquet as pq
import pytest
from sqlalchemy import func, select, text

from src.core.database import get_sync_engine, sync_session_factory
from src.models import Job, StepExecution, Version
from src.services.identity import logical_digest
from tests.support.stub_operators import body
from tests.support.version_fixtures import (
    HUMOR_TEST,
    HUMOR_TRAIN,
    OTHER_SOURCE,
    BuildDriver,
    driver,
    make_source,
)

__all__ = ["driver"]

D, R, V = "/api/v1/datasets", "/api/v1/recipes", "/api/v1/versions"


async def setup(
    client: httpx.AsyncClient,
    recipe_body: dict[str, Any],
    *,
    name: str = "humor",
    target: str = "detector",
) -> tuple[str, str]:
    ds = await client.post(D, json={"name": name, "target_type": target})
    assert ds.status_code == 201, ds.text
    rec = await client.post(R, json={"name": f"{name}-recipe", "body": recipe_body})
    assert rec.status_code == 201, rec.text
    return ds.json()["id"], rec.json()["head_revision_id"]


async def request(
    client: httpx.AsyncClient,
    dataset_id: str,
    revision_id: str,
    inputs: list[dict[str, Any]],
    **extra: Any,
) -> httpx.Response:
    payload = {
        "dataset_id": dataset_id,
        "recipe_revision_id": revision_id,
        "inputs": inputs,
        **extra,
    }
    return await client.post(V, json=payload)


def src(source_id: str) -> dict[str, str]:
    return {"kind": "source", "source_id": source_id}


async def build(
    client: httpx.AsyncClient,
    driver: BuildDriver,
    dataset_id: str,
    revision_id: str,
    inputs: list[dict[str, Any]],
    **extra: Any,
) -> dict[str, Any]:
    response = await request(client, dataset_id, revision_id, inputs, **extra)
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    status = driver.run(job_id)
    with sync_session_factory()() as db:
        job = db.get(Job, job_id)
        assert job is not None
        assert status == "completed", (job.error, job.result)
        version_id = job.result["version_id"]  # type: ignore[index]
    got = await client.get(f"{V}/{version_id}")
    assert got.status_code == 200
    data: dict[str, Any] = got.json()
    return data


class TestBuild:
    async def test_a_build_writes_files_manifest_and_row(
        self, client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
    ) -> None:
        source = make_source(data_dir, {"train": HUMOR_TRAIN, "test": HUMOR_TEST})
        ds, rev = await setup(client, body(("stub_drop_short", {"min_len": 8}), ("stub_keep", {})))
        version = await build(client, driver, ds, rev, [src(source)], seed=20261005)
        assert version["number"] == 1 and version["is_head"] is True
        assert version["seed"] == 20261005 and version["created_by"] == operator_name
        assert {s["name"] for s in version["splits"]} == {"train", "test"}
        assert version["total_rows"] == len(HUMOR_TRAIN) + len(HUMOR_TEST) - 2  # short, tiny
        assert version["column_roles"]["text"] == "content"
        assert version["column_roles"]["label"] == "metadata"
        for split in version["splits"]:
            path = data_dir / split["path"]
            assert hashlib.sha256(path.read_bytes()).hexdigest() == split["file_sha256"]
            assert logical_digest(path) == split["logical_digest"]
            assert pq.ParquetFile(path).metadata.num_rows == split["rows"]
        manifest = await client.get(f"{V}/{version['id']}/manifest")
        assert manifest.headers["etag"] == version["manifest_sha256"]
        assert hashlib.sha256(manifest.content).hexdigest() == version["manifest_sha256"]
        on_disk = (data_dir / "versions" / version["id"] / "manifest.json").read_bytes()
        assert on_disk == manifest.content
        document = json.loads(manifest.content)
        assert document["sources"][0]["licence_display"] == "cc-by-2.0"
        assert document["recipe"]["hash"] == version["recipe_hash"]
        drop = version["drop_summary"]
        assert drop[0]["dropped"] == 2 and drop[0]["reasons"][0]["reason_code"] == "too_short"
        assert drop[0]["rows_in"] == 13 and drop[0]["rows_out"] == 11
        assert [e[1] for e in driver.emitted][-1] == "version_build:completed"

    async def test_rows_carry_system_columns_and_occurrences(
        self, client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
    ) -> None:
        source = make_source(data_dir, {"train": HUMOR_TRAIN})
        ds, rev = await setup(client, body(("stub_keep", {})))
        version = await build(client, driver, ds, rev, [src(source)])
        table = pq.read_table(data_dir / version["splits"][0]["path"]).to_pylist()
        chicken = [r for r in table if r["text"].startswith("Why did the chicken")]
        assert [r["_dw_occurrence"] for r in chicken] == [0, 1]
        assert chicken[0]["_dw_row_key"] == chicken[1]["_dw_row_key"]
        assert chicken[1]["_dw_source_locator"] == "train:1"
        assert chicken[0]["_dw_source_id"] == source and chicken[0]["_dw_origin"] == "source"
        trump = [r["_dw_row_key"] for r in table if r["text"].startswith("Trump")]
        assert len(set(trump)) == 2, "spacing is content: 'Trump 's' and 'Trump's' are two keys"
        assert version["warnings"][0]["code"] == "duplicate_metadata_disagrees"
        assert version["warnings"][0]["details"]["columns"] == {"note": 1}

    async def test_the_same_request_returns_the_existing_version_and_builds_nothing(
        self, client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
    ) -> None:
        source = make_source(data_dir, {"train": HUMOR_TRAIN})
        ds, rev = await setup(client, body(("stub_keep", {})))
        version = await build(client, driver, ds, rev, [src(source)], seed=7)
        executions = driver.stubs.executions
        again = await request(client, ds, rev, [src(source)], seed=7)
        assert again.status_code == 200 and again.json()["id"] == version["id"]
        assert driver.stubs.executions == executions
        with sync_session_factory()() as db:
            assert (
                db.scalar(select(func.count()).select_from(Job).where(Job.kind == "version_build"))
                == 1
            )

    async def test_concurrent_identical_requests_make_one_job(
        self, client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
    ) -> None:
        source = make_source(data_dir, {"train": HUMOR_TRAIN})
        ds, rev = await setup(client, body(("stub_keep", {})))
        a, b = await asyncio.gather(
            request(client, ds, rev, [src(source)], seed=3),
            request(client, ds, rev, [src(source)], seed=3),
        )
        assert a.status_code == b.status_code == 202
        assert a.json()["job_id"] == b.json()["job_id"]
        assert sorted([a.json()["existing_job"], b.json()["existing_job"]]) == [False, True]

    async def test_a_seed_is_defaulted_and_recorded(
        self, client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
    ) -> None:
        source = make_source(data_dir, {"train": HUMOR_TRAIN})
        ds, rev = await setup(client, body(("stub_keep", {})))
        response = await request(client, ds, rev, [src(source)])
        seed = response.json()["seed"]
        assert 0 <= seed < 2**31
        with sync_session_factory()() as db:
            job = db.get(Job, response.json()["job_id"])
            assert job is not None and job.params["seed"] == seed

    async def test_input_order_is_part_of_the_request(
        self, client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
    ) -> None:
        one = make_source(data_dir, {"train": HUMOR_TRAIN})
        two = make_source(data_dir, {"train": OTHER_SOURCE}, repo_id="org/other")
        ds, rev = await setup(client, body(("stub_keep", {})))
        a = await request(client, ds, rev, [src(one), src(two)], seed=1)
        b = await request(client, ds, rev, [src(two), src(one)], seed=1)
        assert a.json()["request_digest"] != b.json()["request_digest"]
        assert a.json()["job_id"] != b.json()["job_id"]

    async def test_a_child_version_reads_its_parent(
        self, client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
    ) -> None:
        source = make_source(data_dir, {"train": HUMOR_TRAIN})
        ds, rev = await setup(client, body(("stub_keep", {})))
        parent = await build(client, driver, ds, rev, [src(source)], seed=1)
        rec2 = await client.post(
            R, json={"name": "drop", "body": body(("stub_drop_short", {"min_len": 8}))}
        )
        child = await build(
            client,
            driver,
            ds,
            rec2.json()["head_revision_id"],
            [{"kind": "version", "version_id": parent["id"]}],
            seed=1,
        )
        assert child["number"] == 2 and child["parent_version_id"] == parent["id"]
        assert child["is_head"] and child["total_rows"] == parent["total_rows"] - 1
        older = (await client.get(f"{V}/{parent['id']}")).json()
        assert older["is_head"] is False and older["superseded_by"] == 2
        manifest = json.loads((await client.get(f"{V}/{child['id']}/manifest")).content)
        assert manifest["sources"][0]["source_id"] == source  # licences travel through lineage


class TestRefusals:
    async def test_a_source_that_is_not_ready_is_refused(
        self, client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
    ) -> None:
        source = make_source(data_dir, {"train": HUMOR_TRAIN})
        with get_sync_engine().begin() as conn:
            conn.execute(
                text("UPDATE dw_sources SET state = 'deleted' WHERE id = :id"), {"id": source}
            )
        ds, rev = await setup(client, body(("stub_keep", {})))
        response = await request(client, ds, rev, [src(source)])
        assert (
            response.status_code == 409 and response.json()["error"]["code"] == "source_not_ready"
        )

    async def test_an_invalid_recipe_is_refused_before_a_job_exists(
        self, client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
    ) -> None:
        source = make_source(data_dir, {"train": HUMOR_TRAIN})
        ds, rev = await setup(client, body(("stub_band", {"min": 0.3, "max": 0.7})))
        driver.stubs.not_allowed.add("stub_band")
        response = await request(client, ds, rev, [src(source)])
        assert response.status_code == 422 and response.json()["error"]["code"] == "recipe_invalid"
        with sync_session_factory()() as db:
            assert db.scalar(select(func.count()).select_from(Job)) == 0

    async def test_an_archived_recipe_is_refused(
        self, client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
    ) -> None:
        source = make_source(data_dir, {"train": HUMOR_TRAIN})
        ds, rev = await setup(client, body(("stub_keep", {})))
        recipe_id = (await client.get(f"{R}?archived=true")).json()["items"][0]["id"]
        await client.post(f"{R}/{recipe_id}/archive")
        response = await request(client, ds, rev, [src(source)])
        assert response.status_code == 409 and response.json()["error"]["code"] == "recipe_archived"

    @pytest.mark.parametrize("kind", ["label_run", "generation_run"])
    async def test_a_binding_nobody_can_resolve_is_refused(
        self,
        client: httpx.AsyncClient,
        driver: BuildDriver,
        data_dir: Path,
        operator_name: str,
        kind: str,
    ) -> None:
        source = make_source(data_dir, {"train": HUMOR_TRAIN})
        ds, rev = await setup(client, body(("stub_keep", {})))
        response = await request(
            client, ds, rev, [src(source)], bindings=[{"kind": kind, "id": "run_1"}]
        )
        assert (
            response.status_code == 404 and response.json()["error"]["code"] == "binding_not_found"
        )

    async def test_an_incomplete_binding_is_refused(
        self,
        client: httpx.AsyncClient,
        driver: BuildDriver,
        data_dir: Path,
        operator_name: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from src.services import bindings

        async def resolver(db: Any, run_id: str) -> dict[str, Any]:
            return {"complete": False}

        monkeypatch.setitem(bindings.BINDING_RESOLVERS, "label_run", resolver)
        source = make_source(data_dir, {"train": HUMOR_TRAIN})
        ds, rev = await setup(client, body(("stub_keep", {})))
        response = await request(
            client, ds, rev, [src(source)], bindings=[{"kind": "label_run", "id": "r"}]
        )
        assert (
            response.status_code == 409 and response.json()["error"]["code"] == "binding_incomplete"
        )

    async def test_no_content_column_is_refused(
        self, client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
    ) -> None:
        source = make_source(data_dir, {"train": HUMOR_TRAIN}, text_columns=())
        ds, rev = await setup(client, body(("stub_keep", {})))
        response = await request(client, ds, rev, [src(source)])
        assert (
            response.status_code == 422 and response.json()["error"]["code"] == "no_content_columns"
        )
        override = await request(client, ds, rev, [src(source)], column_roles={"text": "content"})
        assert override.status_code == 202

    async def test_a_tampered_source_file_fails_the_build_naming_the_file(
        self, client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
    ) -> None:
        source = make_source(data_dir, {"train": HUMOR_TRAIN})
        (data_dir / "sources" / source / "train.parquet").write_bytes(b"PAR1 tampered")
        ds, rev = await setup(client, body(("stub_keep", {})))
        response = await request(client, ds, rev, [src(source)])
        job_id = response.json()["job_id"]
        assert driver.run(job_id) == "failed"
        with sync_session_factory()() as db:
            job = db.get(Job, job_id)
            assert job is not None and job.result is not None
            assert job.result["error"]["code"] == "source_hash_mismatch"
            assert "split 'train'" in job.result["error"]["details"]["file"]
            assert db.scalar(select(func.count()).select_from(Version)) == 0

    async def test_a_reserved_column_is_refused_by_assembly_and_by_the_database(
        self, data_dir: Path, operator_name: str
    ) -> None:
        """The source table's CHECK refuses such a file at import; assembly refuses it again,
        so a file that bypassed 001's import still cannot become a version."""
        import pyarrow as pa

        from src.services.assembly import BuildRefusal, InputFile, assemble

        path = data_dir / "bad.parquet"
        pq.write_table(pa.table({"text": ["x"], "_dw_secret": ["y"]}), path)
        item = InputFile(path, "", "train", "source", "s", "source s split 'train'")
        with pytest.raises(BuildRefusal) as info:
            assemble([item], {"text": "content"}, "dw.rowkey/v1", data_dir / "out")
        assert info.value.code == "reserved_column"
        with pytest.raises(Exception, match="no_reserved_columns"):
            make_source(data_dir, {"train": [dict(r, _dw_secret="x") for r in HUMOR_TRAIN]})

    async def test_conflicting_column_types_fail_the_build(
        self, client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
    ) -> None:
        one = make_source(data_dir, {"train": HUMOR_TRAIN})
        two = make_source(
            data_dir,
            {"train": [{"text": "x y z w", "label": "funny", "score": 0.1, "note": None}]},
            repo_id="o/t",
        )
        ds, rev = await setup(client, body(("stub_keep", {})))
        job_id = (await request(client, ds, rev, [src(one), src(two)])).json()["job_id"]
        assert driver.run(job_id) == "failed"
        with sync_session_factory()() as db:
            job = db.get(Job, job_id)
            assert job is not None and job.result["error"]["code"] == "input_schema_conflict"  # type: ignore[index]


class TestStepFailures:
    async def _run(
        self,
        client: httpx.AsyncClient,
        driver: BuildDriver,
        data_dir: Path,
        recipe_body: dict[str, Any],
    ) -> dict[str, Any]:
        source = make_source(data_dir, {"train": HUMOR_TRAIN})
        ds, rev = await setup(client, recipe_body)
        job_id = (await request(client, ds, rev, [src(source)])).json()["job_id"]
        assert driver.run(job_id) == "failed"
        with sync_session_factory()() as db:
            job = db.get(Job, job_id)
            assert job is not None and job.result is not None
            assert db.scalar(select(func.count()).select_from(Version)) == 0
            error: dict[str, Any] = job.result["error"]
            return error

    async def test_a_drop_without_a_reason_fails_the_build(
        self, client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
    ) -> None:
        error = await self._run(client, driver, data_dir, body(("stub_bad_drop", {})))
        assert error["code"] == "unreasoned_event"
        assert "Step 1 (stub_bad_drop 1)" in error["message"]

    async def test_a_lost_row_fails_accounting(
        self, client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
    ) -> None:
        error = await self._run(client, driver, data_dir, body(("stub_lose_row", {})))
        assert error["code"] == "accounting_mismatch"
        assert error["details"]["direction"] == "input_row_lost"
        assert error["details"]["sample"]

    async def test_a_wrong_key_fails_verification(
        self, client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
    ) -> None:
        error = await self._run(client, driver, data_dir, body(("stub_wrong_key", {})))
        assert error["code"] == "row_key_mismatch"

    async def test_a_generated_row_in_a_held_out_split_fails(
        self, client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
    ) -> None:
        error = await self._run(
            client,
            driver,
            data_dir,
            body(("stub_split", {"fraction": 1.0}), ("stub_generate", {"count": 2})),
        )
        assert error["code"] == "held_out_contains_generated"

    async def test_failed_step_executions_are_marked_and_not_reused(
        self, client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
    ) -> None:
        await self._run(client, driver, data_dir, body(("stub_bad_drop", {})))
        with sync_session_factory()() as db:
            states = dict(
                db.execute(select(StepExecution.kind, StepExecution.state)).all()  # type: ignore[arg-type]
            )
        assert states == {"assemble": "completed", "operator": "failed"}


async def test_recipe_build_delegates_to_the_build_service_once_with_the_head_revision(
    client: httpx.AsyncClient,
    driver: BuildDriver,
    data_dir: Path,
    operator_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task 4.7: payload and call count, not merely "was called"."""
    from unittest.mock import AsyncMock

    from src.services import version_build_service

    source = make_source(data_dir, {"train": HUMOR_TRAIN})
    ds, rev = await setup(client, body(("stub_keep", {})))
    recipe_id = (await client.get(R)).json()["items"][0]["id"]
    spy = AsyncMock(side_effect=version_build_service.request_build)
    monkeypatch.setattr(version_build_service, "request_build", spy)
    response = await client.post(
        f"{R}/{recipe_id}/build", json={"dataset_id": ds, "inputs": [src(source)], "seed": 8}
    )
    assert response.status_code == 202, response.text
    assert spy.await_count == 1
    sent = spy.await_args.args[1]
    assert sent.recipe_revision_id == rev and sent.dataset_id == ds and sent.seed == 8
    assert [i.model_dump() for i in sent.inputs] == [src(source)]


async def test_sources_and_licences_travel_through_two_levels_of_lineage(
    client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
) -> None:
    """Task 7.6: a grandchild's manifest lists the source its grandparent read, licence intact."""
    source = make_source(data_dir, {"train": HUMOR_TRAIN})
    other = make_source(data_dir, {"train": OTHER_SOURCE}, repo_id="org/other")
    ds, rev = await setup(client, body(("stub_keep", {})))
    v1 = await build(client, driver, ds, rev, [src(source)], seed=1)
    v2 = await build(
        client, driver, ds, rev, [{"kind": "version", "version_id": v1["id"]}, src(other)], seed=1
    )
    v3 = await build(client, driver, ds, rev, [{"kind": "version", "version_id": v2["id"]}], seed=1)
    manifest = json.loads((await client.get(f"{V}/{v3['id']}/manifest")).content)
    by_id = {s["source_id"]: s for s in manifest["sources"]}
    assert set(by_id) == {source, other}
    assert all(s["licence_display"] == "cc-by-2.0" and s["revision"] for s in by_id.values())


async def test_version_list_marks_the_head_and_supersession_after_three_versions(
    client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
) -> None:
    """Task 13.3, and the card fields of 13.4."""
    source = make_source(data_dir, {"train": HUMOR_TRAIN})
    ds, rev = await setup(client, body(("stub_keep", {})))
    built = [await build(client, driver, ds, rev, [src(source)], seed=s) for s in (1, 2, 3)]
    listed = (await client.get(V, params={"dataset_id": ds})).json()
    assert listed["total"] == 3
    by_number = {v["number"]: v for v in listed["items"]}
    assert by_number[3]["is_head"] and by_number[3]["superseded_by"] is None
    assert [by_number[n]["superseded_by"] for n in (1, 2)] == [3, 3]
    card = (await client.get(D)).json()["items"][0]
    assert card["head_number"] == 3 and card["head_version_id"] == built[2]["id"]
    assert card["rows"] == built[2]["total_rows"] and card["bytes"] == built[2]["total_bytes"]
    assert card["versions"] == 3 and card["state"] == "ready" and card["target_type"] == "detector"
    assert card["warnings_count"] == 1  # the duplicate-metadata warning of this fixture
    await client.request("DELETE", f"{V}/{built[2]['id']}", json={"reason": "bad"})
    listed = (await client.get(V, params={"dataset_id": ds})).json()
    by_number = {v["number"]: v for v in listed["items"]}
    assert by_number[2]["is_head"] and not by_number[3]["is_head"]
