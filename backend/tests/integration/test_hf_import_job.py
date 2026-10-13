"""The HF import job end to end with an injected loader (001 FTASKS 7.1–7.7; FR-001.1, .3, .6–.10).

Route → ``source_import`` job → ``import_source`` (the real task code, in-process) → loader →
Parquet under ``staging/<job>/`` → rename to ``sources/<id>/`` → file rows → ``ready``. Only the
network and ``load_dataset`` are replaced.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from src.services.sources import hf_materialise
from src.services.sources.hashing import hash_file
from src.services.sources.reader import locator
from tests.support.hf_mock import COLBERT, COLBERT_SHA, SCRIPTED, FakeLoader
from tests.support.source_fixtures import HfEnv, file_rows, hf_env, source_row, sources_where

__all__ = ["hf_env"]
URL = "/api/v1/sources/hf"


async def start(client: httpx.AsyncClient, **body: Any) -> str:
    response = await client.post(URL, json={"repo_id": COLBERT, **body})
    assert response.status_code == 202, response.text
    assert response.json()["source_id"] is None
    return str(response.json()["job_id"])


async def test_a_three_split_import_writes_hashed_parquet_and_becomes_ready(
    client: httpx.AsyncClient, hf_env: HfEnv, data_dir: Path, operator_name: str
) -> None:
    job_id = await start(client)
    [result] = hf_env.run_imports()
    job = hf_env.job(job_id)
    assert job.status == "completed", job.error
    source = source_row(result["source_id"])
    assert source.state == "ready" and source.ready_at is not None
    assert source.resolved_commit == COLBERT_SHA and source.requested_ref is None
    assert source.config == "default" and source.split_selection is None
    assert (source.licence_raw, source.licence_display) == ("cc-by-2.0", "cc-by-2.0")
    assert source.created_by == operator_name and source.created_by_origin == "operator"
    assert source.token_tier == "none" and source.import_job_id == job_id
    assert set(source.library_versions) == {"datasets", "huggingface_hub", "pyarrow"}
    assert source.detection["suggested_target"] == "detector"
    files = {f.split: f for f in file_rows(source.id)}
    assert set(files) == {"train", "validation", "test"}
    for split, rows in (("train", 30), ("validation", 5), ("test", 7)):
        on_disk = data_dir / files[split].path
        assert files[split].path == f"sources/{source.id}/{split}.parquet"
        assert files[split].sha256 == hash_file(on_disk)
        assert files[split].rows == rows and files[split].bytes == on_disk.stat().st_size
    assert locator("validation", 4) == "validation:4"
    assert not (data_dir / "staging" / job_id).exists()
    assert not (data_dir / "runs" / job_id / "hf_cache").exists()
    events = [e for _, e, _ in hf_env.emitted]
    assert events[-1] == "source_import:completed" and "source_import:progress" in events


async def test_the_loader_is_called_once_at_the_commit_without_remote_code(
    client: httpx.AsyncClient, hf_env: HfEnv, data_dir: Path, operator_name: str
) -> None:
    job_id = await start(client, revision="main")
    hf_env.run_imports()
    [call] = hf_env.loader.calls
    assert call["path"] == COLBERT and call["revision"] == COLBERT_SHA
    assert call["trust_remote_code"] is False
    assert call["name"] == "default" and call["split"] is None
    assert Path(call["cache_dir"]) == data_dir / "runs" / job_id / "hf_cache"
    assert source_row(sources_where(import_job_id=job_id)[0].id).requested_ref == "main"


async def test_split_names_are_kept_exactly_and_unsafe_ones_get_a_safe_file_name(
    client: httpx.AsyncClient, hf_env: HfEnv, data_dir: Path, operator_name: str
) -> None:
    hf_env.loader.splits = {
        "train": [{"text": "a sentence long enough", "label": 1}],
        "dev 2023/v1": [{"text": "another long sentence", "label": 0}],
    }
    await start(client)
    [result] = hf_env.run_imports()
    files = {f.split: f for f in file_rows(result["source_id"])}
    assert set(files) == {"train", "dev 2023/v1"}
    assert files["dev 2023/v1"].path.endswith("/split_1.parquet")
    assert (data_dir / files["dev 2023/v1"].path).exists()


async def test_a_requested_split_imports_only_that_split(
    client: httpx.AsyncClient, hf_env: HfEnv, operator_name: str
) -> None:
    await start(client, split="train")
    [result] = hf_env.run_imports()
    assert [f.split for f in file_rows(result["source_id"])] == ["train"]
    assert source_row(result["source_id"]).split_selection == "train"
    assert hf_env.loader.calls[0]["split"] == "train"


async def test_a_loading_script_is_refused_before_any_download(
    client: httpx.AsyncClient, hf_env: HfEnv, operator_name: str
) -> None:
    job_id = await start(client, repo_id=SCRIPTED)
    hf_env.run_imports()
    job = hf_env.job(job_id)
    assert job.status == "failed"
    assert job.result["error"]["code"] == "remote_code_refused"
    assert hf_env.loader.calls == []
    assert sources_where(import_job_id=job_id) == []


async def test_insufficient_space_names_both_sizes(
    client: httpx.AsyncClient,
    hf_env: HfEnv,
    operator_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Usage:
        free = 1_000_000

    monkeypatch.setattr(hf_materialise.shutil, "disk_usage", lambda _p: Usage())
    job_id = await start(client)
    hf_env.run_imports()
    job = hf_env.job(job_id)
    error = job.result["error"]
    assert job.status == "failed" and error["code"] == "insufficient_space"
    assert error["details"] == {"required_bytes": 24_026_194, "free_bytes": 1_000_000}
    assert hf_env.loader.calls == []
    [source] = sources_where(import_job_id=job_id)
    assert source.state == "failed" and source.error["code"] == "insufficient_space"


async def test_over_the_confirmation_level_needs_confirm_large(
    client: httpx.AsyncClient,
    hf_env: HfEnv,
    operator_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert (
        await client.put("/api/v1/settings/import_confirm_bytes", json={"value": "1000"})
    ).status_code == 200
    refused = await start(client)
    hf_env.run_imports()
    assert hf_env.job(refused).result["error"]["code"] == "import_confirmation_required"
    assert hf_env.loader.calls == []
    confirmed = await start(client, confirm_large=True)
    hf_env.run_imports()
    job = hf_env.job(confirmed)
    assert job.status == "completed" and job.params["confirm_large"] is True


@pytest.mark.parametrize(
    ("raised", "code"),
    [
        ("gated", "hf_gated"),
        ("missing", "hf_not_found"),
        ("revision", "revision_unresolved"),
        ("other", "import_failed"),
    ],
)
async def test_a_loader_failure_fails_the_job_with_a_mapped_code(
    client: httpx.AsyncClient, hf_env: HfEnv, operator_name: str, raised: str, code: str
) -> None:
    from huggingface_hub import errors as hf_errors

    response = httpx.Response(403, request=httpx.Request("GET", "https://huggingface.co/x"))
    errors: dict[str, BaseException] = {
        "gated": hf_errors.GatedRepoError("gated", response=response),
        "missing": hf_errors.RepositoryNotFoundError("missing", response=response),
        "revision": hf_errors.RevisionNotFoundError("rev", response=response),
        "other": ValueError("/home/secret/path leaked into a message"),
    }
    hf_env.loader.raises = errors[raised]
    job_id = await start(client)
    hf_env.run_imports()
    job = hf_env.job(job_id)
    assert job.status == "failed" and job.result["error"]["code"] == code
    assert "/home/secret" not in str(job.result) and "/home/secret" not in (job.error or "")
    [source] = sources_where(import_job_id=job_id)
    assert source.state == "failed" and source.error["code"] == code


async def test_an_unexpected_failure_after_the_download_fails_the_job_not_strands_it(
    client: httpx.AsyncClient,
    hf_env: HfEnv,
    data_dir: Path,
    operator_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("disk vanished")

    monkeypatch.setattr(hf_materialise, "write_split", broken)
    job_id = await start(client)
    hf_env.run_imports()
    job = hf_env.job(job_id)
    assert job.status == "failed" and job.result["error"]["code"] == "import_failed"
    [source] = sources_where(import_job_id=job_id)
    assert source.state == "failed"
    assert not (data_dir / "staging" / job_id).exists()
    assert not (data_dir / "sources" / source.id).exists()


async def test_an_operator_token_is_used_once_as_per_import_and_never_stored(
    client: httpx.AsyncClient, hf_env: HfEnv, operator_name: str
) -> None:
    token = "hf_per_import_token_42"
    job_id = await start(client, access_token=token)
    job = hf_env.job(job_id)
    assert job.params["token_supplied"] is True and token not in repr(job.params)
    [result] = hf_env.run_imports()
    assert hf_env.loader.calls[0]["token"] == token
    assert f"Bearer {token}" in hf_env.mock.auth_headers
    assert source_row(result["source_id"]).token_tier == "per_import"
    assert token not in repr(hf_env.sent) and token not in repr(hf_env.emitted)


async def test_the_stored_token_is_used_when_none_is_entered(
    client: httpx.AsyncClient, hf_env: HfEnv, operator_name: str
) -> None:
    stored = "hf_stored_settings_token_7"
    assert (
        await client.put("/api/v1/settings/hf_token", json={"value": stored})
    ).status_code == 200
    await start(client)
    [result] = hf_env.run_imports()
    assert hf_env.loader.calls[0]["token"] == stored
    assert source_row(result["source_id"]).token_tier == "stored"


async def test_an_expired_per_import_token_fails_and_never_uses_the_stored_one(
    client: httpx.AsyncClient, hf_env: HfEnv, operator_name: str
) -> None:
    from src.core import ephemeral_secrets

    await client.put("/api/v1/settings/hf_token", json={"value": "hf_stored_token_never_used"})
    job_id = await start(client, access_token="hf_soon_expired_token")
    ephemeral_secrets.take(job_id)  # the TTL ran out before the worker started
    hf_env.run_imports()
    job = hf_env.job(job_id)
    assert job.status == "failed" and job.result["error"]["code"] == "token_expired"
    assert hf_env.loader.calls == []
    assert set(hf_env.mock.auth_headers) <= {None}


async def test_the_job_is_kind_source_import_and_appears_as_active_while_queued(
    client: httpx.AsyncClient, hf_env: HfEnv, operator_name: str
) -> None:
    job_id = await start(client)
    job = hf_env.job(job_id)
    assert job.kind == "source_import" and job.started_by == operator_name
    assert hf_env.sent == [("midataworks.sources.import_source", [job_id])]


def test_the_space_rule_is_two_point_two_times() -> None:
    with pytest.raises(Exception) as info:
        hf_materialise.preflight(1_000, confirm_large=False, confirm_bytes=10**12, free=2_199)
    assert info.value.code == "insufficient_space"  # type: ignore[attr-defined]
    hf_materialise.preflight(1_000, confirm_large=False, confirm_bytes=10**12, free=2_200)


def test_an_unknown_size_needs_confirmation() -> None:
    with pytest.raises(Exception) as info:
        hf_materialise.preflight(None, confirm_large=False, confirm_bytes=10**12, free=10**15)
    assert info.value.code == "import_confirmation_required"  # type: ignore[attr-defined]
    hf_materialise.preflight(None, confirm_large=True, confirm_bytes=10**12, free=10**15)


def test_fake_loader_default_has_three_splits() -> None:
    assert set(FakeLoader().splits) == {"train", "validation", "test"}


async def test_a_failed_rename_never_leaves_a_ready_source(
    client: httpx.AsyncClient,
    hf_env: HfEnv,
    data_dir: Path,
    operator_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-001.31: ``ready`` only AFTER the files are renamed into place. If the rename fails the
    source must not be ready (it would name files nobody can read); control M6."""
    real_rename = Path.rename

    def failing(self: Path, target: Any) -> Any:
        if "sources" in Path(target).parts:
            raise OSError("disk went away")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", failing)
    job_id = await start(client)
    hf_env.run_imports()
    assert hf_env.job(job_id).status == "failed"
    [source] = sources_where(import_job_id=job_id)
    assert source.state == "failed" and source.ready_at is None
    assert file_rows(source.id) == []
