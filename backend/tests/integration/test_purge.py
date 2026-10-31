"""Purge: the operator's permanent delete of archived items (operator decision 2026-10-20).

Real PostgreSQL, real build pipeline (``driver``), real files on a private data volume, every
route over HTTP through the live app. What each test pins:

- an archived dataset goes with everything that belongs only to it, rows AND files, and leaves one
  tombstone with counts and bytes; the plan lists exactly what the purge removes and deletes
  nothing;
- a dataset, run or approval that is not archived is refused ``409 not_archived``;
- anything outside the purge set still using it refuses ``409 purge_blocked`` naming the blocker
  (a version of another dataset built from it, a publish record, a detector set, a running job, a
  kept label run deriving from a purged one);
- an agent is refused ``403`` on every route and nothing changes;
- a batch purges each item on its own, retries an item blocked only by another item of the same
  batch, and reports per item; it purges runs and approvals before datasets, so each named item
  gets its own tombstone (the live 2026-10-21 batch, rebuilt); an item an earlier item's cascade
  already removed reports ``purged`` with ``removed_with``, never ``not_found``, and a bogus id
  stays ``not_found``;
- the database still refuses a DELETE from an immutable table outside a purge.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from src.core.clock import utc_now
from src.core.database import get_sync_engine, sync_session_factory
from src.core.ids import new_id
from src.models.approval import Approval
from src.models.generation import GenerationChunk, GenerationRun, GenerationRunJob
from src.models.job import Job
from src.models.label import Label
from src.models.label_run import LabelRun, LabelRunChunk, LabelRunJob
from src.models.recipe import RecipeDraft
from tests.integration.test_version_build import build, setup, src
from tests.support.stub_operators import body
from tests.support.version_fixtures import HUMOR_TRAIN, BuildDriver, driver, make_source

__all__ = ["driver"]

AGENT = {"X-Dataworks-Agent": "agent:dataworks-mcp"}
P = "/api/v1/purge"


# ---- fixtures ---------------------------------------------------------------------------------


def _scalar(sql: str, **params: Any) -> Any:
    with get_sync_engine().connect() as conn:
        return conn.execute(text(sql), params).scalar()


def _scalars(sql: str, **params: Any) -> list[Any]:
    with get_sync_engine().connect() as conn:
        return list(conn.execute(text(sql), params).scalars())


def _count(table: str, where: str = "TRUE", **params: Any) -> int:
    return int(_scalar(f"SELECT count(*) FROM {table} WHERE {where}", **params))  # noqa: S608


def _job(kind: str, status: str = "completed", params: dict[str, Any] | None = None) -> str:
    with sync_session_factory()() as db:
        row = Job(
            id=new_id("job"),
            kind=kind,
            status=status,
            progress=1.0 if status == "completed" else 0.0,
            params=params or {},
            started_by="Test Operator",
            started_by_origin="operator",
        )
        db.add(row)
        db.commit()
        return row.id


def _write(path: Path, rows: int) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist([{"k": i, "t": "x" * 50} for i in range(rows)]), path)
    return path.stat().st_size


def _label_run(
    data_dir: Path,
    version_id: str,
    *,
    parents: list[str] | None = None,
    labels: int = 3,
    approval_id: str | None = None,
) -> str:
    run_id = new_id("lr")
    job_id = _job("label_run", params={"label_run_id": run_id})
    with sync_session_factory()() as db:
        db.add(
            LabelRun(
                id=run_id,
                kind="classifier",
                state="completed",
                input_version_id=version_id,
                field_map={"text": "text"},
                endpoint_snapshot={"role": "classifier"},
                sampling={},
                chunk_size=100,
                parent_run_ids=parents or [],
                approval_id=approval_id,
                labeler_identity={"model_id": "m"},
                labeler_identity_hash="a" * 64,
                labeler_fingerprint="b" * 64,
                counts={},
                started_by="Test Operator",
                started_by_origin="operator",
            )
        )
        db.flush()
        db.add(LabelRunJob(label_run_id=run_id, seq=1, job_id=job_id))
        for i in range(labels):
            db.add(
                Label(
                    label_run_id=run_id,
                    row_key=f"{i:064x}",
                    labeler_fingerprint="b" * 64,
                    outcome="positive",
                    parsed_value=1,
                    steering_state="none",
                    raw_output={"text": "yes"},
                    provisional=False,
                )
            )
        db.add(
            LabelRunChunk(
                label_run_id=run_id,
                chunk_index=0,
                job_id=job_id,
                row_count=labels,
                file_path=f"runs/{run_id}/chunk-000000.parquet",
                file_sha256="0" * 64,
            )
        )
        db.commit()
    _write(data_dir / "runs" / run_id / "chunk-000000.parquet", labels)
    _write(data_dir / "runs" / run_id / "labels.parquet", labels)
    return run_id


def _generation_run(data_dir: Path, version_id: str, held_out: str | None = None) -> str:
    run_id = new_id("gr")
    job_id = _job("generation_run", params={"generation_run_id": run_id})
    with sync_session_factory()() as db:
        db.add(
            GenerationRun(
                id=run_id,
                mode="standard",
                target_type="sft",
                input_version_id=version_id,
                held_out_origin_version_id=held_out or version_id,
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
                state="completed",
                warnings=[],
                counts={},
                started_by="Test Operator",
                started_by_origin="operator",
            )
        )
        db.flush()
        db.add(GenerationRunJob(run_id=run_id, seq=1, job_id=job_id))
        db.add(
            GenerationChunk(
                run_id=run_id,
                stage="respond",
                chunk_index=0,
                job_id=job_id,
                row_count=4,
                path=f"runs/{run_id}/gen/respond-000000.parquet",
                file_sha256="0" * 64,
                committed_at=utc_now(),
            )
        )
        db.commit()
    _write(data_dir / "runs" / run_id / "gen" / "respond-000000.parquet", 4)
    return run_id


def _approval(
    status: str,
    payload: dict[str, Any] | None = None,
    archived: bool = False,
    action: str = "version_delete",
) -> str:
    approval_id = new_id("apr")
    with sync_session_factory()() as db:
        db.add(
            Approval(
                id=approval_id,
                action=action,
                target="DELETE /api/v1/versions/{version_id}",
                summary="test",
                payload=payload or {},
                request_digest="0" * 64,
                requested_by="agent:dataworks-mcp",
                status=status,
                expires_at=utc_now() + timedelta(hours=24),
                decided_by="Test Operator" if status != "pending" else None,
                decided_at=utc_now() if status != "pending" else None,
                archived_at=utc_now() if archived else None,
                archived_by="Test Operator" if archived else None,
                archived_by_origin="operator" if archived else None,
            )
        )
        db.commit()
    return approval_id


def _draft(dataset_id: str) -> str:
    draft_id = str(uuid.uuid4())
    with sync_session_factory()() as db:
        db.add(
            RecipeDraft(
                id=draft_id,
                dataset_id=dataset_id,
                body={},
                step_labels=[],
                inputs=[],
                flow_state={},
                updated_by="Test Operator",
                updated_by_origin="operator",
            )
        )
        db.commit()
    return draft_id


def _files_under(root: Path) -> set[str]:
    return {
        str(Path(d, f).relative_to(root))
        for d, _, files in os.walk(root)
        for f in files
        if "staging" not in Path(d).parts
    }


def _bytes_of(root: Path, relative: set[str]) -> int:
    return sum((root / r).stat().st_size for r in relative)


async def _archive(client: httpx.AsyncClient, kind: str, entity_id: str) -> None:
    path = {
        "dataset": "datasets",
        "label_run": "label-runs",
        "generation_run": "generation-runs",
        "approval": "approvals",
    }[kind]
    response = await client.post(f"/api/v1/{path}/{entity_id}/archive", json={"reason": "test"})
    assert response.status_code == 200, response.text


@pytest.fixture
async def world(
    client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
) -> dict[str, Any]:
    """Two datasets built from one source through the real pipeline: ``gone`` (to purge) with a
    label run, a generation run, a draft, a rejected approval naming its version and a recipe only
    it uses; ``kept`` with its own version, run and recipe. Files exist for every one of them."""
    source = make_source(data_dir, {"train": HUMOR_TRAIN})
    before = _files_under(data_dir)
    gone, gone_rev = await setup(client, body(("stub_drop_short", {"min_len": 8})), name="gone")
    v1 = await build(client, driver, gone, gone_rev, [src(source)], seed=1)
    v2 = await build(client, driver, gone, gone_rev, [src(source)], seed=2)
    gone_files = _files_under(data_dir) - before
    kept, kept_rev = await setup(client, body(("stub_keep", {})), name="kept")
    kv = await build(client, driver, kept, kept_rev, [src(source)], seed=3)
    lr = _label_run(data_dir, v1["id"])
    gr = _generation_run(data_dir, v2["id"])
    kept_lr = _label_run(data_dir, kv["id"])
    draft = _draft(gone)
    apr = _approval("rejected", {"version_id": v1["id"], "body": {"reason": "x"}})
    gone_files |= {
        f for f in _files_under(data_dir) if f.startswith((f"runs/{lr}/", f"runs/{gr}/"))
    }
    # The kept build reuses the source assembly step `gone` ran first: that output is shared.
    with get_sync_engine().connect() as conn:
        shared = [
            str(r[0])
            for r in conn.execute(
                text(
                    "SELECT e.output_dir FROM dw_version_steps s JOIN dw_step_executions e "
                    "ON e.id = s.step_execution_id WHERE s.version_id = CAST(:v AS uuid)"
                ),
                {"v": kv["id"]},
            )
        ]
    shared_files = {f for f in gone_files if any(f.startswith(d + "/") for d in shared)}
    gone_files -= shared_files
    return {
        "source": source,
        "gone": gone,
        "gone_rev": gone_rev,
        "v1": v1["id"],
        "v2": v2["id"],
        "kept": kept,
        "kept_rev": kept_rev,
        "kv": kv["id"],
        "lr": lr,
        "gr": gr,
        "kept_lr": kept_lr,
        "draft": draft,
        "apr": apr,
        "gone_files": gone_files,
        "shared_files": shared_files,
    }


# ---- the purge of an archived dataset ----------------------------------------------------------


async def test_purging_an_archived_dataset_removes_its_rows_files_and_leaves_a_tombstone(
    client: httpx.AsyncClient, world: dict[str, Any], data_dir: Path, operator_name: str
) -> None:
    w = world
    await _archive(client, "dataset", w["gone"])
    kept_files = _files_under(data_dir) - w["gone_files"]
    expected_bytes = _bytes_of(data_dir, w["gone_files"])
    assert w["gone_files"] and any(f.startswith("versions/") for f in w["gone_files"])
    assert any("/steps/" in f for f in w["gone_files"])  # the build's step outputs
    recipe_id = _scalar("SELECT recipe_id FROM dw_recipe_revisions WHERE id = :r", r=w["gone_rev"])

    plan = await client.post(f"{P}/plan", json={"items": [{"kind": "dataset", "id": w["gone"]}]})
    assert plan.status_code == 200, plan.text
    planned = plan.json()["items"][0]
    assert planned["status"] == "purgeable" and planned["blockers"] == []
    assert planned["bytes"] == expected_bytes
    assert _count("dw_versions", "dataset_id = CAST(:d AS uuid)", d=w["gone"]) == 2  # nothing gone

    response = await client.post(f"{P}/dataset/{w['gone']}", json={"reason": "e2e test data"})
    assert response.status_code == 200, response.text
    out = response.json()
    assert out["status"] == "purged" and out["name"] == "gone"
    assert out["counts"] == planned["counts"]
    assert out["bytes"] == expected_bytes and out["paths_left"] == []
    c = out["counts"]
    assert c["datasets"] == 1 and c["versions"] == 2 and c["label_runs"] == 1
    assert c["labels"] == 3 and c["generation_runs"] == 1 and c["drafts"] == 1
    assert c["approvals"] == 1 and c["recipes"] == 1 and c["step_executions"] >= 1
    # Jobs: the second build, the label run and the generation run. The first build stays: the
    # kept version reuses its assembly step, which records that job.
    assert c["row_events"] >= 1 and c["jobs"] == 3

    # Rows: gone.
    assert _count("dw_datasets", "id = CAST(:d AS uuid)", d=w["gone"]) == 0
    assert (
        _count("dw_versions", "id IN (CAST(:a AS uuid), CAST(:b AS uuid))", a=w["v1"], b=w["v2"])
        == 0
    )
    assert _count("dw_label_runs", "id = :r", r=w["lr"]) == 0
    assert _count("dw_labels", "label_run_id = :r", r=w["lr"]) == 0
    assert _count("dw_generation_runs", "id = :r", r=w["gr"]) == 0
    assert _count("dw_generation_chunks", "run_id = :r", r=w["gr"]) == 0
    assert _count("dw_recipe_drafts", "id = CAST(:d AS uuid)", d=w["draft"]) == 0
    assert _count("dw_approvals", "id = :a", a=w["apr"]) == 0
    assert _count("dw_recipes", "id = CAST(:r AS uuid)", r=recipe_id) == 0
    assert _count("dw_version_builds", "dataset_id = CAST(:d AS uuid)", d=w["gone"]) == 0
    # Files: gone, and nothing else touched (the assembly output the kept version reuses stays).
    assert _files_under(data_dir) == kept_files
    assert w["shared_files"] and w["shared_files"] <= kept_files
    for rel in w["gone_files"]:
        assert not (data_dir / rel).exists(), rel
    # The kept dataset still reads, with its rows.
    assert (await client.get(f"/api/v1/datasets/{w['kept']}")).status_code == 200
    rows = await client.get(f"/api/v1/versions/{w['kv']}/rows")
    assert rows.status_code == 200 and rows.json()["total"] > 0
    assert _count("dw_label_runs", "id = :r", r=w["kept_lr"]) == 1
    assert (data_dir / "runs" / w["kept_lr"] / "labels.parquet").exists()
    # The tombstone: one append-only row with who, why, counts and bytes.
    with get_sync_engine().connect() as conn:
        rows_ = (
            conn.execute(
                text("SELECT * FROM dw_purge_events WHERE entity_id = :d"), {"d": w["gone"]}
            )
            .mappings()
            .all()
        )
    assert len(rows_) == 1
    t = rows_[0]
    assert t["id"] == out["tombstone_id"] and t["entity_kind"] == "dataset"
    assert t["name"] == "gone" and t["purged_by"] == operator_name
    assert t["purged_by_origin"] == "operator" and t["reason"] == "e2e test data"
    assert t["counts"] == out["counts"] and t["bytes_removed"] == expected_bytes
    with pytest.raises(DBAPIError, match="append-only"):
        with get_sync_engine().begin() as conn:
            conn.execute(text("DELETE FROM dw_purge_events"))
    # Gone means gone.
    again = await client.post(f"{P}/dataset/{w['gone']}")
    assert again.status_code == 404


async def test_a_shared_step_execution_and_a_shared_recipe_stay(
    client: httpx.AsyncClient, world: dict[str, Any], driver: BuildDriver, data_dir: Path
) -> None:
    """A step execution another dataset's version reuses, and a recipe it builds with, are not
    the purged dataset's alone: both stay, with their files."""
    w = world
    other, _ = await setup(client, body(("stub_keep", {})), name="other")
    reused = await build(client, driver, other, w["gone_rev"], [src(w["source"])], seed=1)
    shared = _scalar(
        "SELECT step_execution_id::text FROM dw_version_steps WHERE version_id = CAST(:v AS uuid)",
        v=reused["id"],
    )
    output = _scalar(
        "SELECT output_dir FROM dw_step_executions WHERE id = CAST(:s AS uuid)", s=shared
    )
    await _archive(client, "dataset", w["gone"])
    response = await client.post(f"{P}/dataset/{w['gone']}")
    assert response.status_code == 200, response.text
    assert _count("dw_step_executions", "id = CAST(:s AS uuid)", s=shared) == 1
    assert (data_dir / output).exists()
    assert _count("dw_recipe_revisions", "id = CAST(:r AS uuid)", r=w["gone_rev"]) == 1
    assert "recipes" not in response.json()["counts"]
    rows = await client.get(f"/api/v1/versions/{reused['id']}/rows")
    assert rows.status_code == 200 and rows.json()["total"] > 0


# ---- refusals ----------------------------------------------------------------------------------


async def test_an_item_that_is_not_archived_is_refused(
    client: httpx.AsyncClient, world: dict[str, Any], data_dir: Path
) -> None:
    w = world
    apr = _approval("rejected")
    for kind, entity_id in (
        ("dataset", w["gone"]),
        ("label_run", w["lr"]),
        ("generation_run", w["gr"]),
        ("approval", apr),
    ):
        response = await client.post(f"{P}/{kind}/{entity_id}")
        assert response.status_code == 409, (kind, response.text)
        assert response.json()["error"]["code"] == "not_archived", kind
    assert _count("dw_versions", "dataset_id = CAST(:d AS uuid)", d=w["gone"]) == 2
    assert _count("dw_labels", "label_run_id = :r", r=w["lr"]) == 3
    assert _count("dw_approvals", "id = :a", a=apr) == 1
    assert _count("dw_purge_events") == 0
    for rel in w["gone_files"]:
        assert (data_dir / rel).exists(), rel


async def test_a_version_of_another_dataset_built_from_it_blocks_and_names_it(
    client: httpx.AsyncClient, world: dict[str, Any], driver: BuildDriver, data_dir: Path
) -> None:
    w = world
    child = await build(
        client, driver, w["kept"], w["kept_rev"], [{"kind": "version", "version_id": w["v1"]}]
    )
    await _archive(client, "dataset", w["gone"])
    response = await client.post(f"{P}/dataset/{w['gone']}")
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "purge_blocked"
    blockers = error["details"]["blockers"]
    lineage = [b for b in blockers if b["table"] == "dw_version_inputs"]
    assert lineage and child["id"] in lineage[0]["ids"]
    assert f"kept v{child['number']}" in lineage[0]["message"]
    assert "kept" in error["message"]
    assert _count("dw_versions", "dataset_id = CAST(:d AS uuid)", d=w["gone"]) == 2
    assert _count("dw_purge_events") == 0
    for rel in w["gone_files"]:
        assert (data_dir / rel).exists(), rel


async def test_a_publish_record_blocks_and_is_named(
    client: httpx.AsyncClient, world: dict[str, Any]
) -> None:
    w = world
    job = _job("publish")
    with get_sync_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO dw_publish_builds (id, version_id, projection_digest, projection, "
                "status, started_by, started_by_origin) VALUES ('pb_t', CAST(:v AS uuid), :d, "
                "'{}', 'completed', 'Test Operator', 'operator')"
            ),
            {"v": w["v1"], "d": "0" * 64},
        )
        conn.execute(
            text(
                "INSERT INTO dw_publishes (id, job_id, version_id, build_id, kind, repo_id, "
                "requested_visibility, status, card_prose, request_digest, started_by, "
                "started_by_origin, cancel_too_late, target_kind) VALUES ('pub_t', :j, "
                "CAST(:v AS uuid), 'pb_t', 'publish', 'org/x', 'private', 'failed', '', :d, "
                "'Test Operator', 'operator', false, 'dataset')"
            ),
            {"j": job, "v": w["v1"], "d": "0" * 64},
        )
    await _archive(client, "dataset", w["gone"])
    response = await client.post(f"{P}/dataset/{w['gone']}")
    assert response.status_code == 409, response.text
    blockers = response.json()["error"]["details"]["blockers"]
    tables = {b["table"] for b in blockers}
    assert {"dw_publishes", "dw_publish_builds"} <= tables
    publish = next(b for b in blockers if b["table"] == "dw_publishes")
    assert publish["message"].startswith("publish record pub_t") and "pub_t" in publish["ids"]


async def test_a_detector_set_blocks(client: httpx.AsyncClient, world: dict[str, Any]) -> None:
    w = world
    with get_sync_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO dw_detector_sets (id, name, description, positive_meaning, archived, "
                "created_by, created_by_origin) VALUES ('dts_t', 'humor-set', '', 'funny', false, "
                "'Test Operator', 'operator')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO dw_detector_set_roles (id, set_id, role, position, version_id, split, "
                "input_column, label_column, label_mapping, display_name, label_source_columns) "
                "VALUES ('dsr_t', 'dts_t', 'train', 0, CAST(:v AS uuid), 'train', 'text', 'label', "
                "'{}', 'train', '[]')"
            ),
            {"v": w["v2"]},
        )
    await _archive(client, "dataset", w["gone"])
    response = await client.post(f"{P}/dataset/{w['gone']}")
    assert response.status_code == 409
    blockers = response.json()["error"]["details"]["blockers"]
    assert any(
        b["table"] == "dw_detector_set_roles" and b["message"].startswith("detector set")
        for b in blockers
    )


async def test_a_running_job_blocks(client: httpx.AsyncClient, world: dict[str, Any]) -> None:
    w = world
    await _archive(client, "dataset", w["gone"])
    job = _job("curation_report", status="running", params={"version_id": w["v1"]})
    response = await client.post(f"{P}/dataset/{w['gone']}")
    assert response.status_code == 409
    blockers = response.json()["error"]["details"]["blockers"]
    assert [b for b in blockers if b["code"] == "job_running" and job in b["ids"]]


async def test_a_pending_approval_naming_it_blocks(
    client: httpx.AsyncClient, world: dict[str, Any]
) -> None:
    w = world
    pending = _approval("pending", {"version_id": w["v2"]})
    await _archive(client, "dataset", w["gone"])
    response = await client.post(f"{P}/dataset/{w['gone']}")
    assert response.status_code == 409
    blockers = response.json()["error"]["details"]["blockers"]
    assert [b for b in blockers if b["code"] == "approval_pending" and pending in b["ids"]]


# ---- runs and approvals ------------------------------------------------------------------------


async def test_purging_an_archived_label_run_removes_its_labels_and_files(
    client: httpx.AsyncClient, world: dict[str, Any], data_dir: Path
) -> None:
    w = world
    await _archive(client, "label_run", w["kept_lr"])
    run_dir = data_dir / "runs" / w["kept_lr"]
    size = sum(p.stat().st_size for p in run_dir.rglob("*") if p.is_file())
    response = await client.post(f"{P}/label_run/{w['kept_lr']}")
    assert response.status_code == 200, response.text
    out = response.json()
    assert out["counts"]["labels"] == 3 and out["counts"]["label_runs"] == 1
    assert out["counts"]["label_chunks"] == 1 and out["bytes"] == size
    assert not run_dir.exists()
    assert _count("dw_labels", "label_run_id = :r", r=w["kept_lr"]) == 0
    assert _count("dw_versions", "id = CAST(:v AS uuid)", v=w["kv"]) == 1  # its input stays
    assert _count("dw_purge_events", "entity_kind = 'label_run'") == 1


async def test_a_kept_label_run_deriving_from_a_run_blocks_it(
    client: httpx.AsyncClient, world: dict[str, Any], data_dir: Path
) -> None:
    w = world
    derived = _label_run(data_dir, w["kv"], parents=[w["kept_lr"]])
    await _archive(client, "label_run", w["kept_lr"])
    response = await client.post(f"{P}/label_run/{w['kept_lr']}")
    assert response.status_code == 409
    blockers = response.json()["error"]["details"]["blockers"]
    assert [b for b in blockers if b["code"] == "derived_run" and derived in b["ids"]]
    assert (data_dir / "runs" / w["kept_lr"]).exists()


async def test_purging_an_archived_generation_run_removes_its_rows_and_files(
    client: httpx.AsyncClient, world: dict[str, Any], data_dir: Path
) -> None:
    w = world
    await _archive(client, "generation_run", w["gr"])
    response = await client.post(f"{P}/generation_run/{w['gr']}")
    assert response.status_code == 200, response.text
    assert response.json()["counts"]["generation_chunks"] == 1
    assert not (data_dir / "runs" / w["gr"]).exists()
    assert _count("dw_generation_runs", "id = :r", r=w["gr"]) == 0
    assert _count("dw_versions", "id = CAST(:v AS uuid)", v=w["v2"]) == 1


async def test_purging_an_archived_approval_removes_the_row(
    client: httpx.AsyncClient, world: dict[str, Any]
) -> None:
    apr = _approval("rejected")
    await _archive(client, "approval", apr)
    response = await client.post(f"{P}/approval/{apr}")
    assert response.status_code == 200, response.text
    assert response.json()["counts"] == {"approvals": 1}
    assert _count("dw_approvals", "id = :a", a=apr) == 0


# ---- agents ------------------------------------------------------------------------------------


async def test_an_agent_is_refused_on_every_route_and_nothing_changes(
    client: httpx.AsyncClient, world: dict[str, Any], data_dir: Path
) -> None:
    w = world
    await _archive(client, "dataset", w["gone"])
    item = {"kind": "dataset", "id": w["gone"]}
    for method_path, payload in (
        (f"{P}/dataset/{w['gone']}", None),
        (P, {"items": [item]}),
        (f"{P}/plan", {"items": [item]}),
    ):
        response = await client.post(method_path, json=payload, headers=AGENT)
        assert response.status_code == 403, (method_path, response.text)
        assert response.json()["error"]["code"] == "agent_forbidden"
    assert _count("dw_versions", "dataset_id = CAST(:d AS uuid)", d=w["gone"]) == 2
    assert _count("dw_purge_events") == 0
    for rel in w["gone_files"]:
        assert (data_dir / rel).exists(), rel


def test_no_mcp_tool_reaches_a_purge_route_and_each_is_exempt() -> None:
    from tests.support.mcp_ledger import EXPECTED_CALLS, ROUTE_EXEMPT

    purge_routes = {
        ("POST", "/api/v1/purge"),
        ("POST", "/api/v1/purge/plan"),
        ("POST", "/api/v1/purge/{}/{}"),
    }
    for key in purge_routes:
        assert ROUTE_EXEMPT[key] == "operator-only permanent delete"
    called = {(method, path) for method, path, _args, _params in EXPECTED_CALLS.values()}
    assert len(called) > 100  # the ledger was read
    assert not [c for c in called if "/purge" in c[1]], called


async def test_the_live_mcp_registry_has_no_purge_tool() -> None:
    from src.mcp_server.config import MCPSettings
    from src.mcp_server.server import build_server

    server, _ = build_server(MCPSettings(auth_token="x" * 32))
    names = {t.name for t in await server.list_tools()}
    assert "dataworks_archive_dataset" in names  # archive is served to agents; purge is not
    assert names, "the registry read nothing"
    assert not [n for n in names if "purge" in n or "permanent" in n]


# ---- batch -------------------------------------------------------------------------------------


async def test_a_batch_purges_each_item_on_its_own_and_reports_per_item(
    client: httpx.AsyncClient, world: dict[str, Any], driver: BuildDriver, data_dir: Path
) -> None:
    w = world
    # `kept` gets a version built from `gone`: purging `gone` first is blocked by `kept`, so the
    # batch must retry it after `kept` has gone.
    await build(
        client, driver, w["kept"], w["kept_rev"], [{"kind": "version", "version_id": w["v1"]}]
    )
    shown = _approval("rejected")
    archived_apr = _approval("rejected", archived=True)
    await _archive(client, "dataset", w["gone"])
    await _archive(client, "dataset", w["kept"])
    items = [
        {"kind": "dataset", "id": w["gone"]},
        {"kind": "approval", "id": shown},
        {"kind": "dataset", "id": w["kept"]},
        {"kind": "approval", "id": archived_apr},
        {"kind": "label_run", "id": "lr_missing"},
    ]
    response = await client.post(P, json={"items": items, "reason": "cleanup"})
    assert response.status_code == 200, response.text
    out = response.json()
    status = [(i["id"], i["status"], i["code"]) for i in out["items"]]
    assert status == [
        (w["gone"], "purged", None),
        (shown, "blocked", "not_archived"),
        (w["kept"], "purged", None),
        (archived_apr, "purged", None),
        ("lr_missing", "blocked", "not_found"),
    ]
    assert out["purged"] == 3 and out["blocked"] == 2
    assert _count("dw_datasets") == 0
    assert _count("dw_approvals", "id = :a", a=shown) == 1
    assert _count("dw_purge_events") == 3
    assert not (data_dir / "versions").exists() or not any((data_dir / "versions").iterdir())


def _publish_record(version_id: str) -> None:
    """A failed publish of ``version_id``: publish records are permanent, so it blocks."""
    job = _job("publish")
    build_id, pub_id = new_id("pbld"), new_id("pub")
    with get_sync_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO dw_publish_builds (id, version_id, projection_digest, projection, "
                "status, started_by, started_by_origin) VALUES (:b, CAST(:v AS uuid), :d, "
                "'{}', 'completed', 'Test Operator', 'operator')"
            ),
            {"b": build_id, "v": version_id, "d": "0" * 64},
        )
        conn.execute(
            text(
                "INSERT INTO dw_publishes (id, job_id, version_id, build_id, kind, repo_id, "
                "requested_visibility, status, card_prose, request_digest, started_by, "
                "started_by_origin, cancel_too_late, target_kind) VALUES (:p, :j, "
                "CAST(:v AS uuid), :b, 'publish', 'org/x', 'private', 'failed', '', :d, "
                "'Test Operator', 'operator', false, 'dataset')"
            ),
            {"p": pub_id, "j": job, "v": version_id, "b": build_id, "d": "0" * 64},
        )


async def test_the_live_batch_of_25_reports_24_purged_and_1_blocked_each_with_its_own_tombstone(
    client: httpx.AsyncClient,
    world: dict[str, Any],
    driver: BuildDriver,
    data_dir: Path,
    operator_name: str,
) -> None:
    """The operator's batch of 2026-10-21 00:17 UTC, rebuilt: 10 datasets, then 13 label runs
    over them, then 2 agent requests naming their versions, in the request's order. Production
    answered ``purged 9, blocked 16``: the datasets went first and took their runs and requests
    with them, so the 15 came back ``not_found`` although 24 of 25 were gone. Runs and requests
    now go first, each with its own tombstone, and the one dataset a publish record keeps is the
    only refusal."""
    w = world
    reason = "Operator-directed purge of archived test data (2026-10-20)"
    recipe = body(("stub_keep", {}))

    async def built(name: str, seed: int) -> tuple[str, str]:
        dataset, revision = await setup(client, recipe, name=name)
        version = await build(client, driver, dataset, revision, [src(w["source"])], seed=seed)
        return dataset, version["id"]

    # The datasets, in the live request's order; `gone` and `kept` stand for the first two.
    ctx, ctx_v = w["gone"], w["v1"]
    ctx20 = w["kept"]  # its run, `kept_lr`, is over w["kv"]
    jokes, _ = await setup(client, recipe, name="e2e174-jokes-600")  # never built
    jokes_b, jokes_b_v = await built("e2e174-jokes-600b", 11)
    p3000, p3000_v = await built("e2e174-p07-3000", 12)
    p50000, p50000_v = await built("e2e174-p07-50000", 13)
    novice, _ = await built("novice-humor-2026-10-19", 14)  # a version, no runs
    realhub, realhub_v = await built("ops-8-11-realhub-20261018", 15)
    p5000, p5000_v = await built("ops-p07-5000-20261018", 16)
    p5001, p5001_v = await built("ops-p07-5001-20261018", 17)
    _publish_record(realhub_v)

    # The two agent requests, rejected label-run requests naming a version (as live); a label
    # run records the first, so it can only go after that run.
    apr_3000, apr_5001 = (
        _approval(
            "rejected",
            {"body": {"input_version_id": v, "role": "judge"}},
            archived=True,
            action="agent_label_rows",
        )
        for v in (p3000_v, p5001_v)
    )
    # The 13 label runs. On p07-5001 each run derives from the ones before it (live: 195f from
    # 0b08 from 423b from 1977); the request lists the newest first.
    r1977 = _label_run(data_dir, p5001_v)
    r423b = _label_run(data_dir, p5001_v, parents=[r1977])
    r0b08 = _label_run(data_dir, p5001_v, parents=[r423b, r1977])
    r195f = _label_run(data_dir, p5001_v, parents=[r0b08, r423b, r1977])
    runs = [
        w["lr"],  # ba09, over e2e174-context-overflow
        _label_run(data_dir, p3000_v, labels=30),  # 6a74
        r195f,
        r0b08,
        r423b,
        w["kept_lr"],  # 5ac2, over e2e174-context-overflow-20
        _label_run(data_dir, ctx_v, labels=0),  # a3d0
        r1977,
        _label_run(data_dir, p5000_v, labels=42),  # 57ba
        _label_run(data_dir, p50000_v, labels=0),  # ce72
        _label_run(data_dir, p3000_v, labels=0, approval_id=apr_3000),  # f792
        _label_run(data_dir, p5000_v, labels=0),  # f1c4
        _label_run(data_dir, jokes_b_v, labels=60),  # 68ff
    ]
    datasets = [ctx, ctx20, jokes, jokes_b, p3000, p50000, novice, realhub, p5000, p5001]
    for d in datasets:
        await _archive(client, "dataset", d)
    for r in runs:
        await _archive(client, "label_run", r)
    items = (
        [{"kind": "dataset", "id": d} for d in datasets]
        + [{"kind": "label_run", "id": r} for r in runs]
        + [{"kind": "approval", "id": a} for a in (apr_3000, apr_5001)]
    )
    assert len(items) == 25 and len({i["id"] for i in items}) == 25

    response = await client.post(P, json={"items": items, "reason": reason})
    assert response.status_code == 200, response.text
    out = response.json()
    assert (out["purged"], out["blocked"]) == (24, 1), [
        (i["kind"], i["status"], i["code"]) for i in out["items"]
    ]
    assert [i["id"] for i in out["items"]] == [i["id"] for i in items]  # the request's order
    refused = [i for i in out["items"] if i["status"] == "blocked"]
    assert [(i["id"], i["code"]) for i in refused] == [(realhub, "purge_blocked")]
    assert any(b["table"] == "dw_publishes" for b in refused[0]["blockers"])
    purged = [i for i in out["items"] if i["status"] == "purged"]
    # Each went on its own: its own counts, its own tombstone, nothing folded into a parent.
    for item in purged:
        assert item["code"] is None and item["removed_with"] is None, item
        assert item["tombstone_id"] and item["counts"], item
    by_id = {i["id"]: i for i in out["items"]}
    for r in runs:
        assert by_id[r]["counts"]["label_runs"] == 1
    assert by_id[p3000]["counts"].get("label_runs", 0) == 0  # its runs had their own purge
    assert by_id[apr_3000]["counts"] == {"approvals": 1}

    with get_sync_engine().connect() as conn:
        tombstones = conn.execute(
            text(
                "SELECT id, entity_kind, entity_id, purged_by, purged_by_origin, reason "
                "FROM dw_purge_events"
            )
        ).all()
    assert len(tombstones) == 24
    assert {(t.entity_kind, t.entity_id) for t in tombstones} == {
        (i["kind"], i["id"]) for i in purged
    }
    assert {t.id for t in tombstones} == {i["tombstone_id"] for i in purged}
    assert {(t.purged_by, t.purged_by_origin, t.reason) for t in tombstones} == {
        (operator_name, "operator", reason)
    }
    # And the rows really went: only the refused dataset (and what it keeps) remains.
    assert _count("dw_label_runs") == 0
    assert _count("dw_approvals", "id = ANY(:a)", a=[apr_3000, apr_5001]) == 0
    assert {str(d) for d in _scalars("SELECT id FROM dw_datasets")} == {realhub}
    for r in runs:
        assert not (data_dir / "runs" / r).exists(), r


async def test_an_item_an_earlier_purge_already_removed_reports_purged_with_its_parent(
    client: httpx.AsyncClient, world: dict[str, Any], data_dir: Path, operator_name: str
) -> None:
    """A run and a request in the batch that cannot go on their own (a run outside the batch
    derives from the first and records the second) are removed by their dataset's purge. Asked
    for again they are gone: ``purged``, ``removed_with`` the dataset and that dataset's
    tombstone, counted as purged; a bogus id stays ``not_found``."""
    w = world
    request = _approval(
        "rejected",
        {"body": {"input_version_id": w["v1"]}},
        archived=True,
        action="agent_label_rows",
    )
    # Not in the batch: derives from `lr` and records `request`.
    derived = _label_run(data_dir, w["v1"], parents=[w["lr"]], approval_id=request)
    await _archive(client, "label_run", w["lr"])
    await _archive(client, "dataset", w["gone"])
    items = [
        {"kind": "label_run", "id": w["lr"]},
        {"kind": "approval", "id": request},
        {"kind": "dataset", "id": w["gone"]},
        {"kind": "label_run", "id": "lr_000000000000000000000000"},
    ]
    response = await client.post(P, json={"items": items, "reason": "cleanup"})
    assert response.status_code == 200, response.text
    out = response.json()
    run, approval, dataset, bogus = out["items"]
    assert dataset["status"] == "purged" and dataset["tombstone_id"]
    assert dataset["counts"]["label_runs"] == 2  # the run and the one deriving from it
    assert run["status"] == "purged" and run["code"] == "removed_with", run
    assert run["removed_with"] == {"kind": "dataset", "id": w["gone"], "name": "gone"}
    assert run["tombstone_id"] == dataset["tombstone_id"]
    assert run["bytes"] == 0 and run["counts"] == {}  # the dataset's purge counted them
    assert "gone" in run["message"]
    assert (approval["status"], approval["code"]) == ("purged", "removed_with"), approval
    assert approval["removed_with"] == run["removed_with"]
    assert approval["tombstone_id"] == dataset["tombstone_id"]
    assert (bogus["status"], bogus["code"], bogus["removed_with"]) == (
        "blocked",
        "not_found",
        None,
    )
    assert (out["purged"], out["blocked"]) == (3, 1)
    assert out["bytes"] == dataset["bytes"]
    # Every deleted row is on a tombstone: the dataset's, attributing the operator and reason.
    with get_sync_engine().connect() as conn:
        tombstones = conn.execute(
            text(
                "SELECT id, entity_kind, entity_id, purged_by, reason, counts FROM dw_purge_events"
            )
        ).all()
    assert [(t.id, t.entity_kind, t.entity_id) for t in tombstones] == [
        (dataset["tombstone_id"], "dataset", w["gone"])
    ]
    assert (tombstones[0].purged_by, tombstones[0].reason) == (operator_name, "cleanup")
    assert tombstones[0].counts["label_runs"] == 2
    assert tombstones[0].counts["approvals"] == 2  # `request` and the world's `apr`
    assert _count("dw_label_runs", "id = ANY(:r)", r=[w["lr"], derived]) == 0
    assert _count("dw_approvals", "id = :a", a=request) == 0


async def test_an_id_named_twice_is_not_reported_as_removed_with_itself(
    client: httpx.AsyncClient, world: dict[str, Any]
) -> None:
    """Only ANOTHER item's cascade makes a missing id ``removed_with``: the second mention of a
    purged item is the plain ``not_found`` it always was, not a second purge of the same row."""
    apr = _approval("rejected", archived=True)
    items = [{"kind": "approval", "id": apr}, {"kind": "approval", "id": apr}]
    response = await client.post(P, json={"items": items})
    assert response.status_code == 200, response.text
    out = response.json()
    assert [(i["status"], i["code"], i["removed_with"]) for i in out["items"]] == [
        ("purged", None, None),
        ("blocked", "not_found", None),
    ]
    assert (out["purged"], out["blocked"]) == (1, 1)


async def test_a_plan_reports_blockers_and_deletes_nothing(
    client: httpx.AsyncClient, world: dict[str, Any], data_dir: Path
) -> None:
    w = world
    await _archive(client, "label_run", w["lr"])
    items = [{"kind": "dataset", "id": w["gone"]}, {"kind": "label_run", "id": w["lr"]}]
    response = await client.post(f"{P}/plan", json={"items": items})
    assert response.status_code == 200
    out = response.json()["items"]
    assert out[0]["code"] == "not_archived" and out[1]["status"] == "purgeable"
    assert out[1]["counts"]["labels"] == 3
    assert _count("dw_labels", "label_run_id = :r", r=w["lr"]) == 3
    assert (data_dir / "runs" / w["lr"] / "labels.parquet").exists()


# ---- the database guard ------------------------------------------------------------------------


async def test_an_immutable_table_still_refuses_a_delete_outside_a_purge(
    world: dict[str, Any],
) -> None:
    with pytest.raises(DBAPIError, match="never deleted"):
        with get_sync_engine().begin() as conn:
            conn.execute(
                text("DELETE FROM dw_versions WHERE id = CAST(:v AS uuid)"), {"v": world["v1"]}
            )
    with pytest.raises(DBAPIError, match="append-only"):
        with get_sync_engine().begin() as conn:
            conn.execute(text("DELETE FROM dw_row_events"))
    assert _count("dw_versions", "id = CAST(:v AS uuid)", v=world["v1"]) == 1


async def test_every_table_using_a_purged_table_is_cascaded_or_named() -> None:
    """The blocker check reads the live foreign-key graph; this pins that every table holding a
    key into a purged table is either purged with it or has a name the operator reads."""
    from src.core.database import async_session_factory
    from src.services.purge_service import CASCADE, NOUNS, foreign_keys

    async with async_session_factory()() as db:
        fks = await foreign_keys(db)
    assert ("dw_version_inputs", "input_version_id", "dw_versions", "id") in fks
    unnamed = sorted(
        {child for child, _, parent, _ in fks if parent in CASCADE} - set(CASCADE) - set(NOUNS)
    )
    assert not unnamed, f"tables blocking a purge with no noun: {unnamed}"


async def test_a_dataset_purge_lists_every_run_and_request_it_took_but_not_itself(
    client: httpx.AsyncClient, world: dict[str, Any], operator_name: str
) -> None:
    """What the batch's ``removed_with`` reads: every label run, generation run and request the
    dataset's purge removed, by kind and id, and not the dataset itself."""
    from src.core.agent_origin import Who
    from src.core.database import async_session_factory
    from src.services import purge_service

    w = world
    await _archive(client, "dataset", w["gone"])
    async with async_session_factory()() as db:
        outcome = await purge_service.purge(
            db, Who(operator_name, "operator"), "dataset", w["gone"], None
        )
    assert sorted(outcome.cascaded) == sorted(
        [("label_run", w["lr"]), ("generation_run", w["gr"]), ("approval", w["apr"])]
    )
    assert "cascaded" not in outcome.out()  # internal: the batch reads it, the API does not


async def test_the_service_refuses_an_agent_even_without_the_route(world: dict[str, Any]) -> None:
    """A second layer under the routes: the service itself refuses an agent's purge."""
    from src.core.agent_origin import Who
    from src.core.database import async_session_factory
    from src.core.errors import AppError
    from src.services import purge_service

    async with async_session_factory()() as db:
        with pytest.raises(AppError) as refused:
            await purge_service.purge(
                db, Who("agent:dataworks-mcp", "agent"), "dataset", world["gone"], None
            )
    assert refused.value.status_code == 403 and refused.value.code == "agent_forbidden"
    assert _count("dw_versions", "dataset_id = CAST(:d AS uuid)", d=world["gone"]) == 2


async def test_a_recipe_a_kept_version_uses_stays(
    client: httpx.AsyncClient, world: dict[str, Any]
) -> None:
    """A version of another dataset recorded with the recipe's revision keeps the recipe, even
    with no build request naming it."""
    from tests.support import db_factories

    w = world
    with sync_session_factory()() as db:
        other = db_factories.dataset(db, name="other-recipe-user")
        rev_hash = db.execute(
            text("SELECT recipe_hash FROM dw_recipe_revisions WHERE id = CAST(:r AS uuid)"),
            {"r": w["gone_rev"]},
        ).scalar_one()
        db_factories.version(db, other, recipe_revision_id=w["gone_rev"], recipe_hash=rev_hash)
        db.commit()
    await _archive(client, "dataset", w["gone"])
    response = await client.post(f"{P}/dataset/{w['gone']}")
    assert response.status_code == 200, response.text
    assert "recipes" not in response.json()["counts"]
    assert _count("dw_recipe_revisions", "id = CAST(:r AS uuid)", r=w["gone_rev"]) == 1


async def test_a_recipe_a_kept_failed_build_names_stays(
    client: httpx.AsyncClient, world: dict[str, Any]
) -> None:
    """A build of another dataset that names the revision (failed, so no version) keeps it."""
    w = world
    job = _job("version_build", status="failed")
    with get_sync_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO dw_version_builds (job_id, dataset_id, request_digest, request, "
                "reuse_enabled, sources_verified, steps, current_step_index) VALUES (:j, "
                "CAST(:d AS uuid), :g, CAST(:r AS jsonb), true, false, '[]', 0)"
            ),
            {
                "j": job,
                "d": w["kept"],
                "g": "1" * 64,
                "r": json.dumps({"recipe_revision_id": w["gone_rev"], "inputs": []}),
            },
        )
    await _archive(client, "dataset", w["gone"])
    response = await client.post(f"{P}/dataset/{w['gone']}")
    assert response.status_code == 200, response.text
    assert "recipes" not in response.json()["counts"]
    assert _count("dw_recipe_revisions", "id = CAST(:r AS uuid)", r=w["gone_rev"]) == 1
