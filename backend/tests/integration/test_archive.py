"""Archive (operator decision 2026-10-20; migration 0030).

Archive hides a dataset, label run, generation run or approval from the default lists; every
detail read still works, unarchive restores it, and each change leaves an append-only event.
Refusals: a dataset a live job works on, an unfinished run, an approval that is not rejected or
expired. Every route is exercised over HTTP (the live app), so removing a route, a list filter or
a refusal turns a test here red.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from src.core.clock import utc_now
from src.core.database import get_sync_engine, sync_session_factory
from src.core.ids import new_id
from src.models.approval import Approval
from src.models.archive import ArchiveEvent
from src.models.generation import GenerationRun, GenerationRunJob
from src.models.job import Job
from src.models.label_run import LabelRun, LabelRunJob
from tests.support import db_factories
from tests.support.version_fixtures import driver

__all__ = ["driver"]

AGENT = {"X-Dataworks-Agent": "agent:dataworks-mcp"}


# ---- fixtures ---------------------------------------------------------------------------------


def _dataset_with_version(name: str | None = None) -> tuple[str, str]:
    with sync_session_factory()() as db:
        ds = db_factories.dataset(db, name=name)
        ver = db_factories.version(db, ds)
        db.commit()
        return str(ds.id), str(ver.id)


def _job(status: str, kind: str = "label_run", params: dict[str, Any] | None = None) -> str:
    with sync_session_factory()() as db:
        row = Job(
            id=new_id("job"),
            kind=kind,
            status=status,
            progress=0.0,
            params=params or {},
            started_by="Test Operator",
            started_by_origin="operator",
        )
        db.add(row)
        db.commit()
        return row.id


def _label_run(version_id: str, state: str = "completed", job_status: str | None = None) -> str:
    run_id = new_id("lr")
    with sync_session_factory()() as db:
        db.add(
            LabelRun(
                id=run_id,
                kind="classifier",
                state=state,
                input_version_id=version_id,
                field_map={"text": "text"},
                endpoint_snapshot={"role": "classifier"},
                sampling={},
                chunk_size=100,
                labeler_identity={"model_id": "m"},
                labeler_identity_hash="a" * 64,
                labeler_fingerprint="b" * 64,
                counts={},
                started_by="Test Operator",
                started_by_origin="operator",
            )
        )
        db.flush()
        if job_status is not None:
            job = Job(
                id=new_id("job"),
                kind="label_run",
                status=job_status,
                progress=0.0,
                params={"label_run_id": run_id},
                started_by="Test Operator",
                started_by_origin="operator",
            )
            db.add(job)
            db.flush()
            db.add(LabelRunJob(label_run_id=run_id, seq=1, job_id=job.id))
        db.commit()
    return run_id


def _generation_run(
    version_id: str, state: str = "completed", job_status: str | None = None
) -> str:
    run_id = new_id("gr")
    with sync_session_factory()() as db:
        db.add(
            GenerationRun(
                id=run_id,
                mode="standard",
                target_type="sft",
                input_version_id=version_id,
                held_out_origin_version_id=version_id,
                held_out_splits=["test"],
                prompt_column="text",
                seed_splits=["train"],
                sample_size=10,
                seed=1,
                n_responses=1,
                stages=["respond"],
                generation_endpoint={"role": "generator"},
                server_kind="generic",
                generator_identities=[],
                engine_path="native",
                steering_supported=False,
                state=state,
                warnings=[],
                counts={},
                started_by="Test Operator",
                started_by_origin="operator",
            )
        )
        db.flush()
        if job_status is not None:
            job = Job(
                id=new_id("job"),
                kind="generation_run",
                status=job_status,
                progress=0.0,
                params={"generation_run_id": run_id},
                started_by="Test Operator",
                started_by_origin="operator",
            )
            db.add(job)
            db.flush()
            db.add(GenerationRunJob(run_id=run_id, seq=1, job_id=job.id))
        db.commit()
    return run_id


def _approval(status: str) -> str:
    approval_id = new_id("apr")
    with sync_session_factory()() as db:
        db.add(
            Approval(
                id=approval_id,
                action="version_delete",
                target="DELETE /api/v1/versions/{version_id}",
                summary="test",
                payload={},
                request_digest="0" * 64,
                requested_by="agent:dataworks-mcp",
                status=status,
                expires_at=utc_now() + timedelta(hours=24),
                decided_by="Test Operator" if status != "pending" else None,
                decided_at=utc_now() if status != "pending" else None,
            )
        )
        db.commit()
    return approval_id


def _events(kind: str, entity_id: str) -> list[tuple[str, str, str, str | None]]:
    with sync_session_factory()() as db:
        rows = (
            db.query(ArchiveEvent)
            .filter(ArchiveEvent.entity_kind == kind, ArchiveEvent.entity_id == entity_id)
            .order_by(ArchiveEvent.created_at, ArchiveEvent.id)
            .all()
        )
        return [(r.action, r.actor, r.actor_origin, r.reason) for r in rows]


def _ids(body: dict[str, Any], key: str = "items") -> set[str]:
    return {str(item["id"]) for item in body[key]}


# ---- datasets -----------------------------------------------------------------------------------


async def test_an_archived_dataset_leaves_the_lists_and_still_reads(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    kept, kept_version = _dataset_with_version()
    gone, gone_version = _dataset_with_version()

    archived = await client.post(f"/api/v1/datasets/{gone}/archive", json={"reason": "e2e data"})
    assert archived.status_code == 200, archived.text
    body = archived.json()
    assert body["archived_at"] and body["archived_by"] == "Test Operator"
    assert body["archive_reason"] == "e2e data"

    listed = (await client.get("/api/v1/datasets")).json()
    assert kept in _ids(listed) and gone not in _ids(listed)
    assert listed["total"] == len(listed["items"])
    everything = (await client.get("/api/v1/datasets?include_archived=true")).json()
    assert {kept, gone} <= _ids(everything)

    # Detail reads keep working: the dataset, its version, its version list.
    detail = await client.get(f"/api/v1/datasets/{gone}")
    assert detail.status_code == 200 and detail.json()["archived_at"]
    assert [v["id"] for v in detail.json()["version_list"]] == [gone_version]
    assert (await client.get(f"/api/v1/versions/{gone_version}")).status_code == 200

    # The version pickers leave out an archived dataset's versions unless asked or filtered.
    for order in ("name", "recent"):
        default = (await client.get(f"/api/v1/versions?order={order}")).json()
        assert kept_version in _ids(default) and gone_version not in _ids(default)
        assert default["total"] == len(default["items"])
    wide = (await client.get("/api/v1/versions?include_archived=true")).json()
    assert {kept_version, gone_version} <= _ids(wide)
    own = (await client.get(f"/api/v1/versions?dataset_id={gone}")).json()
    assert _ids(own) == {gone_version}

    restored = await client.post(f"/api/v1/datasets/{gone}/unarchive")
    assert restored.status_code == 200, restored.text
    assert restored.json()["archived_at"] is None and restored.json()["archive_reason"] is None
    assert gone in _ids((await client.get("/api/v1/datasets")).json())
    assert gone_version in _ids((await client.get("/api/v1/versions")).json())
    assert _events("dataset", gone) == [
        ("archive", "Test Operator", "operator", "e2e data"),
        ("unarchive", "Test Operator", "operator", None),
    ]


async def test_archiving_twice_changes_nothing_and_records_once(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    ds, _ = _dataset_with_version()
    first = (await client.post(f"/api/v1/datasets/{ds}/archive")).json()
    second = (await client.post(f"/api/v1/datasets/{ds}/archive", json={"reason": "x"})).json()
    assert first["archived_at"] == second["archived_at"]
    assert second["archive_reason"] is None
    assert len(_events("dataset", ds)) == 1
    shown, _ = _dataset_with_version()
    assert (await client.post(f"/api/v1/datasets/{shown}/unarchive")).status_code == 200
    assert _events("dataset", shown) == []


async def test_an_agent_archive_records_the_agent(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    ds, _ = _dataset_with_version()
    response = await client.post(f"/api/v1/datasets/{ds}/archive", headers=AGENT)
    assert response.status_code == 200, response.text  # not gated: archive deletes nothing
    assert response.json()["archived_by"] == "agent:dataworks-mcp"
    assert _events("dataset", ds) == [("archive", "agent:dataworks-mcp", "agent", None)]


@pytest.mark.parametrize("live", ["queued", "running", "cancelling"])
async def test_a_dataset_a_label_run_job_works_on_is_refused(
    client: httpx.AsyncClient, operator_name: str, live: str
) -> None:
    ds, version = _dataset_with_version()
    _label_run(version, state="running", job_status=live)
    response = await client.post(f"/api/v1/datasets/{ds}/archive")
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "dataset_busy"
    detail = (await client.get(f"/api/v1/datasets/{ds}")).json()
    assert detail["archived_at"] is None
    assert _events("dataset", ds) == []


async def test_a_dataset_whose_jobs_finished_can_be_archived(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    ds, version = _dataset_with_version()
    _label_run(version, state="completed", job_status="completed")
    _generation_run(version, state="failed", job_status="failed")
    _job("failed", kind="diversity_report", params={"version_id": version})
    assert (await client.post(f"/api/v1/datasets/{ds}/archive")).status_code == 200


async def test_a_dataset_a_generation_run_job_works_on_is_refused(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    ds, version = _dataset_with_version()
    _generation_run(version, state="running", job_status="running")
    response = await client.post(f"/api/v1/datasets/{ds}/archive")
    assert response.status_code == 409 and response.json()["error"]["code"] == "dataset_busy"


async def test_a_dataset_a_version_job_works_on_is_refused(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    """A job that names the version in its params (diversity report, verify)."""
    ds, version = _dataset_with_version()
    job = _job("running", kind="diversity_report", params={"version_id": version})
    response = await client.post(f"/api/v1/datasets/{ds}/archive")
    assert response.status_code == 409
    assert response.json()["error"]["details"]["jobs"] == [job]


async def test_a_dataset_a_build_is_writing_is_refused(
    client: httpx.AsyncClient, driver: object, data_dir: object, operator_name: str
) -> None:
    from tests.integration.test_version_build import request, setup, src
    from tests.support.stub_operators import body
    from tests.support.version_fixtures import HUMOR_TRAIN, make_source

    source_id = make_source(data_dir, {"train": HUMOR_TRAIN})  # type: ignore[arg-type]
    dataset, revision = await setup(client, body(("stub_drop_short", {"min_len": 8})))
    queued = await request(client, dataset, revision, [src(source_id)], seed=5)
    assert queued.status_code == 202, queued.text
    response = await client.post(f"/api/v1/datasets/{dataset}/archive")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "dataset_busy"
    assert response.json()["error"]["details"]["jobs"] == [queued.json()["job_id"]]


# ---- label runs ---------------------------------------------------------------------------------


async def test_an_archived_label_run_leaves_the_lists_and_still_reads(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    _, version = _dataset_with_version()
    kept = _label_run(version)
    gone = _label_run(version, state="failed")
    response = await client.post(f"/api/v1/label-runs/{gone}/archive", json={"reason": "test"})
    assert response.status_code == 200, response.text
    assert response.json()["archived_at"] and response.json()["archived_by"] == "Test Operator"

    for query in ("", f"?input_version_id={version}"):
        listed = (await client.get(f"/api/v1/label-runs{query}")).json()
        assert kept in _ids(listed) and gone not in _ids(listed)
        assert listed["total"] == len(listed["items"])
    wide = await client.get(f"/api/v1/label-runs?include_archived=true&input_version_id={version}")
    assert _ids(wide.json()) == {kept, gone}
    detail = await client.get(f"/api/v1/label-runs/{gone}")
    assert detail.status_code == 200 and detail.json()["archive_reason"] == "test"
    assert (await client.get(f"/api/v1/label-runs/{gone}/labels")).status_code == 200

    back = await client.post(f"/api/v1/label-runs/{gone}/unarchive")
    assert back.status_code == 200 and back.json()["archived_at"] is None
    assert gone in _ids((await client.get("/api/v1/label-runs")).json())
    assert [e[0] for e in _events("label_run", gone)] == ["archive", "unarchive"]


@pytest.mark.parametrize("state", ["awaiting_approval", "queued", "running"])
async def test_an_unfinished_label_run_is_refused(
    client: httpx.AsyncClient, operator_name: str, state: str
) -> None:
    _, version = _dataset_with_version()
    run = _label_run(version, state=state)
    response = await client.post(f"/api/v1/label-runs/{run}/archive")
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "run_not_finished"
    assert _events("label_run", run) == []


# ---- generation runs ----------------------------------------------------------------------------


async def test_an_archived_generation_run_leaves_the_lists_and_still_reads(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    _, version = _dataset_with_version()
    kept = _generation_run(version)
    gone = _generation_run(version, state="cancelled")
    response = await client.post(f"/api/v1/generation-runs/{gone}/archive")
    assert response.status_code == 200, response.text
    assert response.json()["archived_at"]

    for query in ("", f"?input_version_id={version}"):
        listed = (await client.get(f"/api/v1/generation-runs{query}")).json()
        assert kept in _ids(listed) and gone not in _ids(listed)
        assert listed["total"] == len(listed["items"])
    wide = (await client.get("/api/v1/generation-runs?include_archived=true")).json()
    assert {kept, gone} <= _ids(wide)
    assert (await client.get(f"/api/v1/generation-runs/{gone}")).json()["archived_at"]

    back = await client.post(f"/api/v1/generation-runs/{gone}/unarchive")
    assert back.status_code == 200 and back.json()["archived_at"] is None
    assert gone in _ids((await client.get("/api/v1/generation-runs")).json())


@pytest.mark.parametrize("state", ["queued", "running"])
async def test_an_unfinished_generation_run_is_refused(
    client: httpx.AsyncClient, operator_name: str, state: str
) -> None:
    _, version = _dataset_with_version()
    run = _generation_run(version, state=state)
    response = await client.post(f"/api/v1/generation-runs/{run}/archive")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "run_not_finished"


# ---- approvals ----------------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["rejected", "expired"])
async def test_a_decided_approval_can_be_archived_and_leaves_the_list(
    client: httpx.AsyncClient, operator_name: str, status: str
) -> None:
    kept = _approval(status)
    gone = _approval(status)
    response = await client.post(f"/api/v1/approvals/{gone}/archive")
    assert response.status_code == 200, response.text
    assert response.json()["archived_at"] and response.json()["status"] == status

    for query in ("", f"?status={status}"):
        listed = (await client.get(f"/api/v1/approvals{query}")).json()
        assert kept in _ids(listed, "approvals") and gone not in _ids(listed, "approvals")
    wide = (await client.get("/api/v1/approvals?include_archived=true")).json()
    assert {kept, gone} <= _ids(wide, "approvals")
    assert (await client.get(f"/api/v1/approvals/{gone}")).json()["archived_at"]

    back = await client.post(f"/api/v1/approvals/{gone}/unarchive")
    assert back.status_code == 200 and back.json()["archived_at"] is None
    assert gone in _ids((await client.get("/api/v1/approvals")).json(), "approvals")


@pytest.mark.parametrize("status", ["pending", "executing", "executed", "failed"])
async def test_an_approval_that_is_not_rejected_or_expired_is_refused(
    client: httpx.AsyncClient, operator_name: str, status: str
) -> None:
    approval = _approval(status)
    response = await client.post(f"/api/v1/approvals/{approval}/archive")
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "approval_not_archivable"
    assert (await client.get(f"/api/v1/approvals/{approval}")).json()["archived_at"] is None


# ---- the trail ----------------------------------------------------------------------------------


async def test_the_archive_trail_is_append_only(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    ds, _ = _dataset_with_version()
    await client.post(f"/api/v1/datasets/{ds}/archive")
    for sql in (
        "UPDATE dw_archive_events SET actor = 'someone else'",
        "DELETE FROM dw_archive_events",
    ):
        with pytest.raises(Exception, match="append"):  # noqa: B017
            with get_sync_engine().begin() as conn:
                conn.execute(text(sql))
    assert len(_events("dataset", ds)) == 1


async def test_an_unknown_item_is_not_found(client: httpx.AsyncClient, operator_name: str) -> None:
    for path in (
        f"/api/v1/datasets/{uuid.uuid4()}/archive",
        "/api/v1/label-runs/lr_missing/archive",
        "/api/v1/generation-runs/gr_missing/archive",
        "/api/v1/approvals/apr_missing/archive",
    ):
        assert (await client.post(path)).status_code == 404, path
