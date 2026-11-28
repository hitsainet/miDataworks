"""A slice's space check (ENH-006 D108): ``data_files`` are sized by those files, at the pinned
commit, not by the whole configuration the Viewer reports. One 4.8 GB file of ``fineweb-2`` read as
772 GB and 20 TB and was refused ``insufficient_space``."""

from __future__ import annotations

import math
from typing import Any

import httpx
import pytest

from src.services.sources import hf_materialise
from tests.support.hf_mock import COLBERT
from tests.support.source_fixtures import HfEnv, hf_env

__all__ = ["hf_env"]
URL = "/api/v1/sources/hf"
TRAIN = "data/train-00000.parquet"
TEST = "data/test-00000.parquet"


class Usage:
    free = 1_000_000


def hub_sizes(env: HfEnv, sizes: dict[str, int]) -> None:
    env.mock.overrides["blobs=true"] = (
        200,
        {"siblings": [{"rfilename": name, "size": size} for name, size in sizes.items()]},
    )


async def start(client: httpx.AsyncClient, **body: Any) -> str:
    response = await client.post(URL, json={"repo_id": COLBERT, **body})
    assert response.status_code == 202, response.text
    job_id: str = response.json()["job_id"]
    return job_id


async def test_a_slice_that_fits_imports_though_its_configuration_would_not(
    client: httpx.AsyncClient,
    hf_env: HfEnv,
    operator_name: str,
    data_dir: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hf_materialise.shutil, "disk_usage", lambda _p: Usage())
    hub_sizes(hf_env, {TRAIN: 1_000, TEST: 999_999_999, "README.md": 999_999_999})
    job_id = await start(client, max_rows=3, data_files=[TRAIN])
    hf_env.run_imports()
    job = hf_env.job(job_id)
    assert job.status == "completed", job.result
    (call,) = hf_env.loader.calls
    assert call["data_files"] == [TRAIN]


async def test_a_slice_too_large_is_refused_by_its_own_size(
    client: httpx.AsyncClient,
    hf_env: HfEnv,
    operator_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hf_materialise.shutil, "disk_usage", lambda _p: Usage())
    hub_sizes(hf_env, {TRAIN: 1_000_000, TEST: 500_000, "README.md": 7})
    job_id = await start(client, max_rows=3, data_files=[TRAIN, TEST])
    hf_env.run_imports()
    job = hf_env.job(job_id)
    error = job.result["error"]
    assert job.status == "failed" and error["code"] == "insufficient_space"
    # 2.2 x the two named files, and neither the README nor the Viewer's whole-configuration figure
    required = math.ceil(hf_materialise.SPACE_FACTOR * 1_500_000)
    assert error["details"] == {"required_bytes": required, "free_bytes": 1_000_000}
    assert hf_env.loader.calls == []


async def test_files_that_match_nothing_ask_for_confirmation(
    client: httpx.AsyncClient, hf_env: HfEnv, operator_name: str
) -> None:
    hub_sizes(hf_env, {TRAIN: 1_000})
    job_id = await start(client, max_rows=3, data_files=["data/absent-*.parquet"])
    hf_env.run_imports()
    job = hf_env.job(job_id)
    assert job.status == "failed"
    assert job.result["error"]["code"] == "import_confirmation_required"
    assert hf_env.loader.calls == []


async def test_max_rows_alone_is_still_sized_by_the_configuration(
    client: httpx.AsyncClient,
    hf_env: HfEnv,
    operator_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without ``data_files`` the loader downloads every file before it keeps the first rows."""
    monkeypatch.setattr(hf_materialise.shutil, "disk_usage", lambda _p: Usage())
    job_id = await start(client, max_rows=3)
    hf_env.run_imports()
    error = hf_env.job(job_id).result["error"]
    assert error["code"] == "insufficient_space"
    assert error["details"]["required_bytes"] == 24_026_194


def test_slice_bytes_counts_each_matching_file_once() -> None:
    sizes = {"a/1.parquet": 10, "a/2.parquet": 20, "b/1.parquet": 40, "a/1.json": 80}
    assert hf_materialise.slice_bytes(sizes, ["a/1.parquet"]) == 10
    assert hf_materialise.slice_bytes(sizes, ["a/*.parquet"]) == 30
    assert hf_materialise.slice_bytes(sizes, ["a/*.parquet", "a/1.parquet"]) == 30  # once
    assert hf_materialise.slice_bytes(sizes, ["*.parquet"]) == 70  # as a path glob reads
    assert hf_materialise.slice_bytes(sizes, ["c/*"]) is None
