"""What a publish made is readable where an agent looks (FU-7a gaps (x), (y), (af), 2026-11-27).

- (x) An executed ``hub_push`` (``apr_8a80a3e0…``, ``apr_25c1ab9f…``) read ``result_kind: null,
  result_id: null``: the publish route answers ``{publish_id, job_id, request_digest}`` and the
  executor only read ``id``. The ``pub_…`` came only from ``dataworks_list_publishes``.
- (y) ``dataworks_list_publishes`` said "state and verification result" and carried neither;
  ``dataworks_get_publish`` had only a per-file ``match``.
- (af) ``dataworks_get_split_download_url`` served the VERSION's split file (with ``_dw_*``
  columns), not the published one, and said nothing of the bytes: a consumer could not check what
  it fetched against the publish.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import httpx
import pyarrow.parquet as pq
from sqlalchemy import update

from src.core.database import sync_session_factory
from src.models import Approval
from tests.support.publish_fixtures import (
    PublishDriver,
    built_version,
    completed_build,
    publish,
    publish_and_run,
    publisher,
    store_token,
)
from tests.support.version_fixtures import BuildDriver, driver

__all__ = ["driver", "publisher"]

AGENT = {"X-Dataworks-Agent": "agent:dataworks-mcp"}


async def _approved_publish(
    client: httpx.AsyncClient, driver: BuildDriver, publisher: PublishDriver, data_dir: Path
) -> tuple[str, str, dict]:
    """(approval id, version id, the publish as read) after an agent publish the operator
    approved and the worker ran."""
    await store_token(client)
    version_id = await built_version(client, driver, data_dir)
    build = await completed_build(client, publisher, version_id)
    pending = await publish(client, publisher, version_id, build["id"], headers=AGENT)
    approval_id = pending.json()["approval_id"]
    approved = await client.post(f"/api/v1/approvals/{approval_id}/approve")
    assert approved.status_code == 200 and approved.json()["status"] == "executed"
    publisher.run_publish_tasks()
    items = (await client.get("/api/v1/publishes", params={"version_id": version_id})).json()
    (row,) = items["items"]
    got = (await client.get(f"/api/v1/publishes/{row['id']}")).json()
    assert got["approval_id"] == approval_id  # the publish row names its approval
    return approval_id, version_id, got


# --- (x) ------------------------------------------------------------------------------------


async def test_an_executed_push_names_the_publish_it_made(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    approval_id, _, pub = await _approved_publish(client, driver, publisher, data_dir)
    status = (await client.get(f"/api/v1/approvals/{approval_id}")).json()
    assert status["status"] == "executed"
    assert (status["result_kind"], status["result_id"]) == ("publish", pub["id"])
    assert pub["id"].startswith("pub_")
    with sync_session_factory()() as db:  # stored at execution, not only computed on read
        row = db.get(Approval, approval_id)
        assert row is not None and (row.result_kind, row.result_id) == ("publish", pub["id"])


async def test_an_older_executed_push_is_named_on_read_and_the_read_writes_nothing(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    """``apr_8a80a3e0…`` executed before the fix and stored nulls: the read finds the publish
    that names the approval (``dw_publishes.approval_id``) and leaves the stored row as it is."""
    approval_id, _, pub = await _approved_publish(client, driver, publisher, data_dir)
    with sync_session_factory()() as db:
        db.execute(
            update(Approval)
            .where(Approval.id == approval_id)
            .values(result_kind=None, result_id=None)
        )
        db.commit()
    status = (await client.get(f"/api/v1/approvals/{approval_id}")).json()
    assert (status["result_kind"], status["result_id"]) == ("publish", pub["id"])
    listed = (await client.get("/api/v1/approvals", params={"status": "executed"})).json()
    (mine,) = [a for a in listed["approvals"] if a["id"] == approval_id]
    assert (mine["result_kind"], mine["result_id"]) == ("publish", pub["id"])
    with sync_session_factory()() as db:
        row = db.get(Approval, approval_id)
        assert row is not None and (row.result_kind, row.result_id) == (None, None)


# --- (y) ------------------------------------------------------------------------------------


async def test_list_and_read_say_verified_from_the_per_file_match(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    await store_token(client)
    version_id = await built_version(client, driver, data_dir)
    build = await completed_build(client, publisher, version_id)
    pub = await publish_and_run(client, publisher, version_id, build["id"])
    assert pub["status"] == "published" and pub["files"]
    assert all(f["match"] is True for f in pub["files"]) and pub["verified"] is True
    listed = (await client.get("/api/v1/publishes", params={"version_id": version_id})).json()
    assert [i["verified"] for i in listed["items"]] == [True]


async def test_a_mismatched_or_uncompared_publish_is_not_verified(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    """The Hub reports another hash for one split (``dw_publish_files`` is insert-only, so the
    mismatch is made where it happens), and a publish still queued has compared nothing."""
    await store_token(client)
    version_id = await built_version(client, driver, data_dir)
    build = await completed_build(client, publisher, version_id)
    publisher.hub.corrupt_path = build["files"][0]["path"]
    bad = await publish_and_run(client, publisher, version_id, build["id"])
    assert any(f["match"] is False for f in bad["files"]), bad
    assert bad["verified"] is False
    publisher.hub.corrupt_path = None
    queued = await publish(client, publisher, version_id, build["id"], repo="mistudio/humor-queued")
    assert queued.status_code == 201, queued.text
    waiting = (await client.get(f"/api/v1/publishes/{queued.json()['publish_id']}")).json()
    assert waiting["files"] == [] and waiting["verified"] is None
    listed = (await client.get("/api/v1/publishes", params={"version_id": version_id})).json()
    by_id = {i["id"]: i["verified"] for i in listed["items"]}
    assert by_id == {bad["id"]: False, queued.json()["publish_id"]: None}


def test_verified_reads_every_file() -> None:
    from src.api.v1.endpoints.publishing import verified_from

    assert verified_from([True, True]) is True
    assert verified_from([True, False]) is False
    assert verified_from([True, None]) is None
    assert verified_from([None, False]) is False
    assert verified_from([]) is None


# --- (af) -----------------------------------------------------------------------------------


async def test_the_default_link_names_the_bytes_it_serves(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    await store_token(client)
    version_id = await built_version(client, driver, data_dir)
    minted = await client.post(f"/api/v1/versions/{version_id}/splits/train/download-url", json={})
    assert minted.status_code == 200, minted.text
    facts = minted.json()
    got = await client.get(facts["path"])
    assert got.status_code == 200
    assert facts["served"] == "version_file"
    assert facts["sha256"] == hashlib.sha256(got.content).hexdigest()
    assert facts["bytes"] == len(got.content)
    assert facts["file_columns"] == pq.read_table(io.BytesIO(got.content)).column_names
    assert "_dw_row_key" in facts["file_columns"]
    projected = (
        await client.post(
            f"/api/v1/versions/{version_id}/splits/train/download-url",
            json={"columns": ["text"]},
        )
    ).json()
    assert projected["served"] == "projection" and projected["sha256"] is None


async def test_a_link_can_serve_the_published_file_byte_for_byte(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    await store_token(client)
    version_id = await built_version(client, driver, data_dir)
    build = await completed_build(client, publisher, version_id)
    pub = await publish_and_run(client, publisher, version_id, build["id"])
    published = next(f for f in pub["files"] if f["split"] == "train")
    assert published["match"] is True

    minted = await client.post(
        f"/api/v1/versions/{version_id}/splits/train/download-url",
        json={"publish_id": pub["id"]},
    )
    assert minted.status_code == 200, minted.text
    facts = minted.json()
    assert facts["served"] == "published_file" and facts["publish_id"] == pub["id"]
    got = await client.get(facts["path"])
    assert got.status_code == 200
    digest = hashlib.sha256(got.content).hexdigest()
    assert digest == published["sha256"] == published["remote_lfs_sha256"] == facts["sha256"]
    default = (
        await client.post(f"/api/v1/versions/{version_id}/splits/train/download-url", json={})
    ).json()
    assert default["sha256"] != digest  # the default link still serves the version's own file
    assert facts["file_columns"] == pq.read_table(io.BytesIO(got.content)).column_names

    refused = [
        ({"publish_id": pub["id"], "columns": ["text"]}, "publish_file_whole"),
        ({"publish_id": pub["id"], "format": "jsonl"}, "publish_file_whole"),
        ({"publish_id": "pub_000000000000000000000000"}, "publish_not_found"),
    ]
    for body, code in refused:
        response = await client.post(
            f"/api/v1/versions/{version_id}/splits/train/download-url", json=body
        )
        assert response.status_code in (404, 409, 422), response.text
        assert response.json()["error"]["code"] == code
