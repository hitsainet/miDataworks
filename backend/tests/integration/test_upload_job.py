"""Uploads end to end (001 FTASKS 8.2, 8.6, 13.5; FR-001.21–001.25, FR-001.27).

One upload with a train Parquet and a test JSONL becomes one source with two splits; the recorded
original SHA-256 equals ``sha256sum``; the licence is "not stated"; a repeat upload returns the
existing source; an upload over the cap is 413 with nothing left in staging; user file names are
data only, never paths.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pyarrow as pa
import pyarrow.parquet as pq

from src.core.config import get_settings
from tests.support.source_fixtures import HfEnv, file_rows, hf_env, source_row, sources_where

__all__ = ["hf_env"]
URL = "/api/v1/sources/uploads"


def _staged(data_dir: Path) -> list[Path]:
    staging = data_dir / "staging"
    return list(staging.iterdir()) if staging.exists() else []


def parquet_bytes(tmp_path: Path) -> bytes:
    path = tmp_path / "train-src.parquet"
    rows = [{"text": f"a long enough joke number {i}", "humor": i % 2 == 0} for i in range(40)]
    pq.write_table(pa.Table.from_pylist(rows), path)
    return path.read_bytes()


JSONL = "\n".join(
    json.dumps({"text": f"another long enough line {i}", "humor": i % 3 == 0}) for i in range(9)
).encode()


async def upload(
    client: httpx.AsyncClient, files: list[tuple[str, bytes, str]], **manifest: Any
) -> httpx.Response:
    plan = {"files": [{"name": n, "split": s} for n, _, s in files], **manifest}
    return await client.post(
        URL,
        files=[("files", (n, b, "application/octet-stream")) for n, b, _ in files],
        data={"manifest": json.dumps(plan)},
    )


async def test_a_parquet_and_a_jsonl_become_one_source_with_two_splits(
    client: httpx.AsyncClient, hf_env: HfEnv, data_dir: Path, operator_name: str, tmp_path: Path
) -> None:
    train = parquet_bytes(tmp_path)
    response = await upload(
        client, [("train.parquet", train, "train"), ("test.jsonl", JSONL, "test")]
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    [result] = hf_env.run_imports()
    assert hf_env.job(job_id).status == "completed"
    source = source_row(result["source_id"])
    assert source.kind == "upload" and source.state == "ready"
    assert source.licence_display == "not stated" and source.licence_origin == "none"
    assert source.display_name == "train.parquet" and source.created_by == operator_name
    assert source.resolved_commit is None and len(source.content_hash) == 64
    files = {f.split: f for f in file_rows(source.id)}
    assert set(files) == {"train", "test"}
    assert files["train"].original_sha256 == hashlib.sha256(train).hexdigest()
    assert files["test"].original_sha256 == hashlib.sha256(JSONL).hexdigest()
    assert files["train"].original_name == "train.parquet" and files["test"].rows == 9
    originals = sorted(p.name for p in (data_dir / "sources" / source.id / "original").iterdir())
    assert len(originals) == 2
    for f in files.values():
        assert (data_dir / f.path).exists()
    assert source.detection["suggested_target"] == "detector"
    assert _staged(data_dir) == []


async def test_a_repeat_upload_returns_the_existing_source(
    client: httpx.AsyncClient, hf_env: HfEnv, operator_name: str, tmp_path: Path
) -> None:
    files = [("test.jsonl", JSONL, "test")]
    await upload(client, files)
    [first] = hf_env.run_imports()
    again = await upload(client, files)
    assert again.status_code == 200, again.text
    assert again.json()["id"] == first["source_id"]
    assert hf_env.sent == []
    renamed = await upload(client, [("other-name.jsonl", JSONL, "test")])
    assert renamed.status_code == 200, "the file name is not part of the identity"


async def test_an_upload_still_importing_returns_its_job(
    client: httpx.AsyncClient, hf_env: HfEnv, operator_name: str
) -> None:
    first = (await upload(client, [("t.jsonl", JSONL, "test")])).json()
    hf_env.sent.clear()
    from src.core.database import sync_session_factory
    from src.models.source import Source
    from src.workers import source_tasks

    with sync_session_factory()() as db:
        job = source_tasks.claim_job(db, first["job_id"])
        assert job is not None
    params = hf_env.job(first["job_id"]).params
    with sync_session_factory()() as db:
        db.add(
            Source(
                id="11111111-1111-1111-1111-111111111111",
                kind="upload",
                state="importing",
                display_name="t.jsonl",
                content_hash=params["content_hash"],
                licence_display="not stated",
                library_versions={},
                import_job_id=first["job_id"],
                created_by="Test Operator",
                created_by_origin="operator",
            )
        )
        db.commit()
    again = await upload(client, [("t.jsonl", JSONL, "test")])
    assert again.status_code == 202
    assert again.json() == {
        "job_id": first["job_id"],
        "source_id": "11111111-1111-1111-1111-111111111111",
        "existing_job": True,
    }


async def test_different_csv_options_are_a_different_source(
    client: httpx.AsyncClient, hf_env: HfEnv, operator_name: str
) -> None:
    csv = b"text;label\nhello there;1\n"
    await upload(client, [("a.csv", csv, "train")])
    await upload(client, [("a.csv", csv, "train")], csv={"delimiter": ";"})
    results = hf_env.run_imports()
    assert len({r["source_id"] for r in results}) == 2


async def test_an_upload_over_the_cap_is_413_and_leaves_nothing_in_staging(
    client: httpx.AsyncClient, hf_env: HfEnv, data_dir: Path, operator_name: str
) -> None:
    assert (
        await client.put("/api/v1/settings/upload_max_bytes", json={"value": "1000"})
    ).status_code == 200
    response = await upload(client, [("big.jsonl", b'{"a": "' + b"x" * 1000 + b'"}', "train")])
    assert response.status_code == 413
    error = response.json()["error"]
    assert error["code"] == "upload_too_large" and error["details"]["limit_bytes"] == 1000
    assert _staged(data_dir) == []
    assert hf_env.sent == [] and sources_where(kind="upload") == []


async def test_the_streaming_cap_holds_when_the_size_is_not_declared(
    data_dir: Path, tmp_path: Path
) -> None:
    import pytest

    from src.core.errors import AppError
    from src.services.sources.upload_service import receive_stream

    class Stream:
        def __init__(self) -> None:
            self.chunks = [b"x" * 600, b"y" * 600, b"z" * 600]

        async def read(self, _n: int) -> bytes:
            return self.chunks.pop(0) if self.chunks else b""

    destination = tmp_path / "staged" / "000.bin"
    with pytest.raises(AppError) as info:
        await receive_stream(Stream(), destination, 1000, "big.bin")
    assert info.value.code == "upload_too_large"
    assert not destination.exists()


def test_the_default_cap_is_two_gibibytes() -> None:
    assert get_settings().upload_max_bytes == 2 * 1024**3


async def test_a_manifest_that_does_not_match_the_files_is_refused(
    client: httpx.AsyncClient, hf_env: HfEnv, operator_name: str
) -> None:
    response = await client.post(
        URL,
        files=[("files", ("a.jsonl", JSONL, "application/octet-stream"))],
        data={"manifest": json.dumps({"files": [{"name": "b.jsonl", "split": "train"}]})},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "upload_manifest_invalid"
    bad = await client.post(
        URL,
        files=[("files", ("a.jsonl", JSONL, "application/octet-stream"))],
        data={"manifest": "not json"},
    )
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "upload_manifest_invalid"


async def test_two_files_may_not_share_a_split(
    client: httpx.AsyncClient, hf_env: HfEnv, operator_name: str
) -> None:
    response = await upload(client, [("a.jsonl", JSONL, "train"), ("b.jsonl", JSONL, "train")])
    assert response.status_code == 422


async def test_a_format_mismatch_is_refused_before_a_job_exists(
    client: httpx.AsyncClient, hf_env: HfEnv, data_dir: Path, operator_name: str
) -> None:
    response = await upload(client, [("a.parquet", b"text,label\nx,1\n", "train")])
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "upload_format_mismatch"
    assert _staged(data_dir) == [] and hf_env.sent == []


async def test_a_malformed_file_fails_the_job_and_leaves_no_source_ready(
    client: httpx.AsyncClient, hf_env: HfEnv, data_dir: Path, operator_name: str
) -> None:
    lines = JSONL.split(b"\n")
    lines[6] = b'{"text": "broken'
    response = await upload(client, [("a.jsonl", b"\n".join(lines), "train")])
    job_id = response.json()["job_id"]
    hf_env.run_imports()
    job = hf_env.job(job_id)
    assert job.status == "failed" and job.result["error"]["details"]["line"] == 7
    [source] = sources_where(import_job_id=job_id)
    assert source.state == "failed"
    assert _staged(data_dir) == []
    assert not (data_dir / "sources" / source.id).exists()


async def test_a_user_file_name_is_data_only_never_a_path(
    client: httpx.AsyncClient, hf_env: HfEnv, data_dir: Path, operator_name: str
) -> None:
    name = "../../escape.jsonl"
    response = await upload(client, [(name, JSONL, "train")])
    assert response.status_code == 202, response.text
    [result] = hf_env.run_imports()
    [f] = file_rows(result["source_id"])
    assert f.original_name == name
    assert f.path == f"sources/{result['source_id']}/train.parquet"
    assert not (data_dir.parent / "escape.jsonl").exists()
    assert not any(p.name == "escape.jsonl" for p in data_dir.parent.rglob("escape.jsonl"))
