"""Repeat imports and identity (001 FTASKS 7.10; FR-001.4, FR-001.5).

A full SHA already imported answers 200 with no job; an empty revision resolving to a ready source
ends ``completed`` with ``existing_source_id`` and writes nothing; two racing inserts give one
source; a newer commit gives a new source and leaves the old one unchanged; two configs of one
repository are two sources.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from src.core.database import sync_session_factory
from src.models.source_enums import SourceKind
from src.services.sources import source_service
from tests.support.hf_mock import COLBERT, COLBERT_SHA, HEAD_SHA, HUMICROEDIT
from tests.support.source_fixtures import HfEnv, file_rows, hf_env, source_row, sources_where

__all__ = ["hf_env"]
URL = "/api/v1/sources/hf"


async def imported(client: httpx.AsyncClient, hf_env: HfEnv, **body: Any) -> dict[str, Any]:
    response = await client.post(URL, json={"repo_id": COLBERT, **body})
    assert response.status_code == 202, response.text
    [result] = hf_env.run_imports()
    return result


async def test_a_full_sha_already_imported_answers_at_once_with_no_job(
    client: httpx.AsyncClient, hf_env: HfEnv, operator_name: str
) -> None:
    first = await imported(client, hf_env)
    response = await client.post(
        URL, json={"repo_id": COLBERT, "config": "default", "revision": COLBERT_SHA}
    )
    assert response.status_code == 200, response.text
    assert response.json()["id"] == first["source_id"]
    assert hf_env.sent == []


async def test_a_full_sha_without_the_config_resolves_in_a_job_that_writes_nothing(
    client: httpx.AsyncClient, hf_env: HfEnv, operator_name: str
) -> None:
    first = await imported(client, hf_env)
    again = await imported(client, hf_env, revision=COLBERT_SHA)
    assert again["existing_source_id"] == first["source_id"]
    assert len(hf_env.loader.calls) == 1


async def test_a_full_sha_whose_source_is_still_importing_returns_its_job(
    client: httpx.AsyncClient, hf_env: HfEnv, operator_name: str
) -> None:
    from src.core.database import sync_session_factory
    from tests.support import db_factories

    with sync_session_factory()() as db:
        job = db_factories.job(db, kind="source_import", status="running")
        row = db_factories.source(db, state="importing", repo_id=COLBERT, commit=COLBERT_SHA)
        row.import_job_id = job.id
        db.commit()
        job_id, source_id = job.id, row.id
    response = await client.post(URL, json={"repo_id": COLBERT, "revision": COLBERT_SHA})
    assert response.status_code == 202
    assert response.json() == {"job_id": job_id, "source_id": source_id, "existing_job": True}
    assert hf_env.sent == []


async def test_an_empty_revision_resolving_to_a_ready_source_writes_nothing(
    client: httpx.AsyncClient, hf_env: HfEnv, data_dir: Path, operator_name: str
) -> None:
    first = await imported(client, hf_env)
    before = sorted(p for p in data_dir.rglob("*") if p.is_file())
    second = await imported(client, hf_env)
    assert second["existing_source_id"] == first["source_id"] and second["existing"] is True
    assert len(hf_env.loader.calls) == 1, "the second import downloaded"
    assert sorted(p for p in data_dir.rglob("*") if p.is_file()) == before
    assert len(sources_where(repo_id=COLBERT)) == 1


async def test_two_queued_requests_for_one_identity_make_one_source(
    client: httpx.AsyncClient, hf_env: HfEnv, operator_name: str
) -> None:
    for _ in range(2):
        assert (await client.post(URL, json={"repo_id": COLBERT})).status_code == 202
    results = hf_env.run_imports()
    assert len(results) == 2
    assert len({r["source_id"] for r in results}) == 1
    assert sorted(r["existing"] for r in results) == [False, True]


def test_an_insert_that_loses_the_race_returns_the_winner(
    clean_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = {
        "kind": SourceKind.HF,
        "repo_id": COLBERT,
        "config": None,
        "split_selection": None,
        "resolved_commit": COLBERT_SHA,
    }
    fields = {
        **identity,
        "display_name": COLBERT,
        "licence_display": "not stated",
        "library_versions": {"datasets": "x"},
        "created_by": "Test Operator",
        "created_by_origin": "operator",
    }
    with sync_session_factory()() as db:
        winner = source_service.create_importing(db, fields, identity)
    real = source_service._live_identity
    calls = {"n": 0}

    def blind_first(session: Any, **kw: Any) -> Any:
        calls["n"] += 1
        return None if calls["n"] == 1 else real(session, **kw)

    monkeypatch.setattr(source_service, "_live_identity", blind_first)
    with sync_session_factory()() as db:
        loser = source_service.create_importing(db, fields, identity)
    assert loser.existing is True and loser.source.id == winner.source.id
    assert len(sources_where(repo_id=COLBERT)) == 1


async def test_a_newer_commit_is_a_new_source_and_the_old_one_is_unchanged(
    client: httpx.AsyncClient, hf_env: HfEnv, operator_name: str
) -> None:
    old = await imported(client, hf_env)
    old_files = [(f.split, f.sha256) for f in file_rows(old["source_id"])]
    hf_env.mock.head = HEAD_SHA
    new = await imported(client, hf_env)
    assert new["source_id"] != old["source_id"] and new["existing"] is False
    assert source_row(new["source_id"]).resolved_commit == HEAD_SHA
    kept = source_row(old["source_id"])
    assert kept.state == "ready" and kept.resolved_commit == COLBERT_SHA
    assert [(f.split, f.sha256) for f in file_rows(old["source_id"])] == old_files


async def test_two_configs_of_one_repository_are_two_sources(
    client: httpx.AsyncClient, hf_env: HfEnv, operator_name: str
) -> None:
    a = await imported(client, hf_env, repo_id=HUMICROEDIT, config="subtask-1")
    b = await imported(client, hf_env, repo_id=HUMICROEDIT, config="subtask-2")
    assert a["source_id"] != b["source_id"]
    assert {source_row(a["source_id"]).config, source_row(b["source_id"]).config} == {
        "subtask-1",
        "subtask-2",
    }
    assert source_row(a["source_id"]).display_name == f"{HUMICROEDIT} (subtask-1)"


async def test_a_failed_import_does_not_block_a_new_one(
    client: httpx.AsyncClient, hf_env: HfEnv, operator_name: str
) -> None:
    hf_env.loader.raises = ValueError("boom")
    await imported(client, hf_env)
    hf_env.loader.raises = None
    second = await imported(client, hf_env)
    assert second["existing"] is False
    states = sorted(s.state for s in sources_where(repo_id=COLBERT))
    assert states == ["failed", "ready"]
