"""Hub import slices (017 FTASKS 7.2, 7.3; FR-017.31; TD-017.20).

``max_rows`` and ``data_files`` import the first rows of the named files at the pinned commit:
deterministic, recorded on the source (``import_slice``) and in the licence facts every publish
reads (the manifest source's ``extensions``). Through the REST route and the live MCP tool's
payload (``tests/support/mcp_ledger.py``), against ``hf_mock`` and the fake loader.
"""

from __future__ import annotations

from typing import Any

import httpx
import pyarrow.parquet as pq

from src.core.database import sync_session_factory
from tests.support.hf_mock import COLBERT, COLBERT_SHA
from tests.support.source_fixtures import HfEnv, hf_env

__all__ = ["hf_env"]
URL = "/api/v1/sources/hf"


async def _import(client: httpx.AsyncClient, hf_env: HfEnv, body: dict[str, Any]) -> dict:
    response = await client.post(URL, json={"repo_id": COLBERT, **body})
    assert response.status_code == 202, response.text
    (result,) = hf_env.run_imports()
    got = await client.get(f"/api/v1/sources/{result['source_id']}")
    assert got.status_code == 200, got.text
    return dict(got.json())


async def test_a_slice_is_the_first_rows_of_the_named_files_and_is_recorded(
    client: httpx.AsyncClient, hf_env: HfEnv, operator_name: str, data_dir: Any
) -> None:
    files = ["data/train-00000.parquet", "data/test-00000.parquet"]
    source = await _import(client, hf_env, {"max_rows": 4, "data_files": list(reversed(files))})
    assert source["state"] == "ready" and source["resolved_commit"] == COLBERT_SHA
    assert source["import_slice"] == {"max_rows": 4, "data_files": sorted(files)}
    (call,) = hf_env.loader.calls
    assert call["data_files"] == sorted(files) and call["revision"] == COLBERT_SHA
    by_split = {f["split"]: f for f in source["files"]}
    for split, rows in hf_env.loader.splits.items():
        stored = pq.read_table(data_dir / by_split[split]["path"]).to_pylist()
        assert by_split[split]["rows"] == min(4, len(rows))
        assert [r["text"] for r in stored] == [r["text"] for r in rows[:4]]  # the FIRST rows


async def test_a_slice_imports_the_same_rows_every_time(
    client: httpx.AsyncClient, hf_env: HfEnv, operator_name: str, data_dir: Any
) -> None:
    first = await _import(client, hf_env, {"max_rows": 3})
    from src.models.source import Source

    with sync_session_factory()() as db:  # the first source is gone; the slice imports again
        row = db.get(Source, first["id"])
        assert row is not None
        row.state = "deleted"
        db.commit()
    second = await _import(client, hf_env, {"max_rows": 3})
    assert second["id"] != first["id"]
    assert [f["sha256"] for f in second["files"]] == [f["sha256"] for f in first["files"]]
    assert "data_files" not in hf_env.loader.calls[-1]  # no files named: none sent


async def test_the_slice_is_in_the_licence_facts_a_publish_reads(
    client: httpx.AsyncClient, hf_env: HfEnv, operator_name: str, data_dir: Any
) -> None:
    from src.models.source import Source
    from src.services.publishing.check_inputs import SourceFacts, manifest_source
    from src.services.publishing.licence_table import classify

    sliced = await _import(client, hf_env, {"max_rows": 2})
    with sync_session_factory()() as db:
        row = db.get(Source, sliced["id"])
        assert row is not None
        verdict = classify(row.licence_raw, [])
        out = manifest_source(SourceFacts(row, verdict, verdict, False))
    assert out["extensions"] == {"import_slice": {"max_rows": 2}}
    assert out["origin"]["revision"] == COLBERT_SHA


async def test_a_bad_slice_is_refused(client: httpx.AsyncClient, operator_name: str) -> None:
    for body in (
        {"max_rows": 0},
        {"data_files": []},
        {"data_files": ["../outside.parquet"]},
        {"data_files": ["/absolute.parquet"]},
    ):
        response = await client.post(URL, json={"repo_id": COLBERT, **body})
        assert response.status_code == 422, (body, response.text)


async def test_a_pinned_commit_already_imported_whole_does_not_answer_a_slice(
    client: httpx.AsyncClient, hf_env: HfEnv, operator_name: str
) -> None:
    """Review R3 survivor: at a full commit SHA the route answers an identical READY source at
    once (200). The whole dataset is not identical to a slice of it, nor the reverse: each starts
    its own import (202), and the same slice again IS answered at once with its own source."""
    # config named: the worker records the resolved config ("default"), and the fast path matches
    # the request's config as given (a pre-existing gap recorded as R3 debt)
    pinned = {"repo_id": COLBERT, "revision": COLBERT_SHA, "config": "default"}
    whole = await client.post(URL, json=pinned)
    assert whole.status_code == 202, whole.text
    (done,) = hf_env.run_imports()
    sliced = await client.post(URL, json={**pinned, "max_rows": 5})
    assert sliced.status_code == 202, sliced.text  # not the whole source
    (made,) = hf_env.run_imports()
    assert made["source_id"] != done["source_id"]
    again = await client.post(URL, json={**pinned, "max_rows": 5})
    assert again.status_code == 200, again.text
    assert again.json()["id"] == made["source_id"]
    other = await client.post(URL, json={**pinned, "max_rows": 6})
    assert other.status_code == 202, other.text
