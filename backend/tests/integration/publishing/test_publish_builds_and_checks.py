"""Publish builds, the check-run preview and the card draft (008 FTASKS 5.3–5.6, 6.5, 7.6, 9.7;
EC-10)."""

from __future__ import annotations

import tracemalloc
from pathlib import Path
from typing import Any

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import update

from src.core.database import sync_session_factory
from src.models import PublishCheckRun, Version
from src.services.publishing import build_service
from src.services.publishing.projection import Counters
from tests.support.publish_fixtures import (
    PublishDriver,
    built_version,
    completed_build,
    publisher,
    store_token,
)
from tests.support.version_fixtures import BuildDriver, driver

__all__ = ["driver", "publisher"]


async def test_a_completed_build_is_reused_and_content_addressed(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    version_id = await built_version(client, driver, data_dir)
    build = await completed_build(client, publisher, version_id)
    assert {f["name"] for f in build["files"]} == {"train", "test"}
    held = {f["name"]: f["held_out"] for f in build["files"]}
    assert held == {"train": False, "test": True}
    for f in build["files"]:
        path = data_dir / "publish" / build["id"] / f["path"]
        assert pq.ParquetFile(path).metadata.num_rows == f["rows"]
        assert len(f["sha256"]) == 64 and len(f["git_blob_sha1"]) == 40
    again = await client.post(
        f"/api/v1/versions/{version_id}/publish-builds", json={"label_column": "label"}
    )
    assert again.status_code == 200 and again.json() == {
        "build_id": build["id"],
        "job_id": None,
        "reused": True,
        "status": "completed",
    }
    other = await client.post(
        f"/api/v1/versions/{version_id}/publish-builds", json={"label_column": None}
    )
    assert other.status_code == 202 and other.json()["build_id"] != build["id"]


async def test_label_counts_and_columns_describe_the_shipped_files(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    version_id = await built_version(client, driver, data_dir)
    build = await completed_build(client, publisher, version_id)
    assert sum(sum(f["label_counts"].values()) for f in build["files"]) == 13
    names = [c["name"] for c in build["columns"]]
    assert (
        names[-3:] == ["_dw_row_key", "_dw_occurrence", "_dw_origin"] and "_dw_split" not in names
    )
    label = next(c for c in build["columns"] if c["name"] == "label")
    assert label["semantic"] == "label" and label["label_values"] == ["0", "1"]


async def test_ec10_a_deleted_or_missing_version_cannot_build(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    missing = await client.post("/api/v1/versions/not-a-uuid/publish-builds", json={})
    assert missing.status_code == 404
    version_id = await built_version(client, driver, data_dir)
    with sync_session_factory()() as db:
        db.execute(
            update(Version)
            .where(Version.id == version_id)
            .values(
                state="deleted",
                deleted_by="Test Operator",
                deleted_by_origin="operator",
                deleted_at=__import__("src.core.clock", fromlist=["utc_now"]).utc_now(),
                delete_reason="test",
            )
        )
        db.commit()
    response = await client.post(f"/api/v1/versions/{version_id}/publish-builds", json={})
    assert (
        response.status_code == 409 and response.json()["error"]["code"] == "version_not_complete"
    )


async def test_an_unknown_label_column_is_refused(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    version_id = await built_version(client, driver, data_dir)
    for column in ("nope", "_dw_row_key"):
        response = await client.post(
            f"/api/v1/versions/{version_id}/publish-builds", json={"label_column": column}
        )
        assert (
            response.status_code == 422
            and response.json()["error"]["code"] == "label_column_unknown"
        )


def test_a_split_streams_in_batches_with_bounded_memory(data_dir: Path, monkeypatch: Any) -> None:
    """FTASKS 5.5: memory is bounded by the batch, not the split, and the writer sees batches."""
    rows = 200_000
    table = pa.table(
        {
            "text": [f"row {i} " + "x" * 80 for i in range(rows)],
            "_dw_row_key": [f"{i:064x}" for i in range(rows)],
            "_dw_occurrence": [0] * rows,
            "_dw_origin": ["source"] * rows,
            "_dw_split": ["train"] * rows,
        }
    )
    source = data_dir / "versions" / "v" / "train.parquet"
    source.parent.mkdir(parents=True)
    pq.write_table(table, source, row_group_size=10_000)
    whole = table.nbytes
    del table
    batches: list[int] = []
    real = build_service.WRITER

    class Spy(real):  # type: ignore[misc, valid-type]
        def write_batch(self, batch: Any, *a: Any, **k: Any) -> None:
            batches.append(batch.num_rows)
            super().write_batch(batch, *a, **k)

    monkeypatch.setattr(build_service, "WRITER", Spy)
    tracemalloc.start()
    build_service.write_split(
        source,
        data_dir / "publish" / "b" / "data" / "train.parquet",
        None,
        None,
        Counters(),
        10_000,
    )
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert len(batches) == 20 and sum(batches) == rows
    assert peak < whole / 4, f"peak {peak:,} bytes against a {whole:,}-byte split"


async def test_the_check_preview_runs_in_the_worker_and_its_snapshot_is_frozen(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    await store_token(client)
    version_id = await built_version(client, driver, data_dir)
    build = await completed_build(client, publisher, version_id)
    response = await client.post(
        f"/api/v1/versions/{version_id}/publish-checks",
        json={"build_id": build["id"], "repo_id": "mistudio/humor-test", "visibility": "public"},
    )
    assert response.status_code == 202, response.text
    publisher.run_publish_tasks()
    run = (await client.get(f"/api/v1/publish-check-runs/{response.json()['check_run_id']}")).json()
    assert run["status"] == "completed" and run["licence_table_version"] == 1
    outcomes = {r["check"]: r["outcome"] for r in run["results"]}
    assert outcomes["C-1"] == "green" and outcomes["C-2"] == "green"
    # 006 is not built (C-5 not_checked → amber); 004 answers for real (2026-10-07) and its
    # findings on this 13-row version depend on the build's seed, so C-3 and C-7 are green or amber.
    assert outcomes["C-5"] == "amber"
    assert outcomes["C-3"] in ("green", "amber") and outcomes["C-7"] in ("green", "amber")
    repo = next(r for r in run["results"] if r["check"] == "repository")
    assert repo["evidence"]["push_allowed"] is False and repo["evidence"]["exists"] is False
    # a later licence annotation does not alter the stored snapshot
    from src.models import Source, SourceAnnotation

    with sync_session_factory()() as db:
        source_id = db.query(Source.id).first()[0]
        db.add(
            SourceAnnotation(
                id=__import__("uuid").uuid4().__str__(),
                source_id=source_id,
                kind="terms",
                redistribution="forbids",
                value={},
                reason="terms say no",
                created_by="Test Operator",
                created_by_origin="operator",
            )
        )
        db.commit()
        stored = db.get(PublishCheckRun, response.json()["check_run_id"])
        assert (
            stored is not None
            and {r["check"]: r["outcome"] for r in stored.results or []}["C-1"] == "green"
        )


async def test_the_card_draft_locks_front_matter_and_record(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    version_id = await built_version(client, driver, data_dir)
    none_yet = await client.get(f"/api/v1/versions/{version_id}/card-draft")
    assert none_yet.status_code == 409 and none_yet.json()["error"]["code"] == "build_required"
    build = await completed_build(client, publisher, version_id)
    draft = (
        await client.get(f"/api/v1/versions/{version_id}/card-draft?repo_id=mistudio/x")
    ).json()
    assert draft["build_id"] == build["id"]
    assert draft["front_matter"]["configs"][0]["data_files"]
    assert "dw:record" in draft["record_markdown"] and "C-2" not in draft["record_markdown"]
    assert draft["prose"].startswith("# humor v1")


async def test_the_built_handoff_manifest_is_served_before_any_publish(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    import json

    from src.services.publishing.manifest_builder import validate_against_file

    version_id = await built_version(client, driver, data_dir)
    before = await client.get(f"/api/v1/versions/{version_id}/handoff-manifest")
    assert before.status_code == 409
    build = await completed_build(client, publisher, version_id)
    served = await client.get(f"/api/v1/versions/{version_id}/handoff-manifest")
    assert served.status_code == 200
    validate_against_file(served.content)
    doc = json.loads(served.content)
    assert doc["publication"] is None and doc["kind"] == "midataworks.dataset-version"
    assert (
        served.content
        == (await client.get(f"/api/v1/versions/{version_id}/handoff-manifest")).content
    )
    assert {s["sha256"] for s in doc["content"]["splits"]} == {f["sha256"] for f in build["files"]}
    assert doc["version"]["created_by_origin"] == "operator"
    text = served.content.decode()
    for forbidden in ("Test Operator", "http://", "https://", "hf_"):
        assert forbidden not in text, forbidden


async def test_an_operator_annotation_flips_c1(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    """001's structured ``redistribution`` annotation decides C-1 (FR-008.59; FTASKS 14.1)."""
    await store_token(client)
    version_id = await built_version(client, driver, data_dir, licence=None)
    build = await completed_build(client, publisher, version_id)

    async def c1() -> str:
        response = await client.post(
            f"/api/v1/versions/{version_id}/publish-checks",
            json={
                "build_id": build["id"],
                "repo_id": "mistudio/humor-test",
                "visibility": "public",
            },
        )
        publisher.run_publish_tasks()
        run = (
            await client.get(f"/api/v1/publish-check-runs/{response.json()['check_run_id']}")
        ).json()
        return {r["check"]: r["outcome"] for r in run["results"]}["C-1"]

    assert await c1() == "amber"
    sources = (await client.get("/api/v1/sources")).json()["items"]
    source_id = next(s["id"] for s in sources if s["repo_id"] == "org/humor")
    noted = await client.post(
        f"/api/v1/sources/{source_id}/annotations",
        json={
            "kind": "terms",
            "redistribution": "permits",
            "value": {"text": "CC0 per the authors"},
            "reason": "read the paper",
        },
    )
    assert noted.status_code in (200, 201), noted.text
    assert await c1() == "green"
