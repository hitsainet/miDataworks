"""A miForge set delivered through the publish primitive to a private Hub repository (suite
tracker 4.4, operator decision D7; FR-008.68; 008 FTASKS 12.4).

Same fake Hub as ``test_publish_fake_hub.py``: real build, real worker bodies, real HubClient.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from src.core.database import sync_session_factory
from src.models import Publish
from src.services.publishing.approval_digest import publish_request_digest
from tests.integration.publishing.test_publish_fake_hub import _ready
from tests.support.publish_fixtures import REPO, PublishDriver, publish, publish_and_run, publisher
from tests.support.version_fixtures import BuildDriver, driver

__all__ = ["driver", "publisher"]

CORPUS = {"target": "miforge_set", "miforge_set_kind": "corpus"}


async def test_a_corpus_set_publishes_privately_with_a_miforge_set_manifest(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    version_id, build = await _ready(client, driver, publisher, data_dir)
    pub = await publish_and_run(client, publisher, version_id, build["id"], target=CORPUS)
    assert pub["status"] == "published", pub["error"]
    assert pub["target_kind"] == "miforge_set" and pub["miforge_set_kind"] == "corpus"
    assert pub["visibility_after"] == "private" and publisher.hub.repos[REPO].private is True
    manifest = json.loads(publisher.hub.head_files(REPO)["midataworks-dataset-version.json"])
    target = manifest["target"]
    assert target["kind"] == "miforge_set" and target["miforge_set_kind"] == "corpus"
    assert target["contract_reference"]["type"] == "corpus"
    assert target["contract_reference"]["version"].startswith("v")
    with sync_session_factory()() as db:
        row = db.get(Publish, pub["id"])
        assert row is not None and row.published_manifest is not None
        assert row.published_manifest["target"] == target
    # A set delivery is not the version's dataset publication (009's send, the handoff manifest).
    served = (await client.get(f"/api/v1/versions/{version_id}/handoff-manifest")).json()
    assert (served.get("publication") or {}).get("state") != "published", served.get("publication")


async def test_a_dataset_publish_still_writes_a_dataset_target(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    version_id, build = await _ready(client, driver, publisher, data_dir)
    pub = await publish_and_run(client, publisher, version_id, build["id"])
    assert pub["target_kind"] == "dataset" and pub["miforge_set_kind"] is None
    manifest = json.loads(publisher.hub.head_files(REPO)["midataworks-dataset-version.json"])
    assert manifest["target"]["kind"] == "dataset"


async def test_a_miforge_set_is_refused_public(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    version_id, build = await _ready(client, driver, publisher, data_dir)
    r = await publish(
        client, publisher, version_id, build["id"], visibility="public", target=CORPUS
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "miforge_set_private_only"
    assert publisher.hub.repos == {}


async def test_a_set_the_files_cannot_form_is_refused_naming_the_column(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    version_id, build = await _ready(client, driver, publisher, data_dir)
    r = await publish(
        client,
        publisher,
        version_id,
        build["id"],
        target={"target": "miforge_set", "miforge_set_kind": "prompt_set"},
    )
    assert r.status_code == 422, r.text
    error = r.json()["error"]
    assert error["code"] == "miforge_set_unformable" and error["details"]["missing"] == ["prompt"]


async def test_a_set_kind_without_the_target_is_refused(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    version_id, build = await _ready(client, driver, publisher, data_dir)
    r = await publish(
        client, publisher, version_id, build["id"], target={"miforge_set_kind": "corpus"}
    )
    assert r.status_code == 422


def test_an_approval_for_a_dataset_never_covers_a_set_and_old_digests_hold() -> None:
    common = {
        "kind": "publish",
        "version_id": "v",
        "build_id": "b",
        "build_files": [{"path": "data/train.parquet", "sha256": "0" * 64}],
        "repo_id": "o/r",
        "visibility": "private",
        "card_prose": "",
    }
    dataset = publish_request_digest(**common)
    assert publish_request_digest(**common, target_kind="dataset") == dataset
    as_set = publish_request_digest(**common, target_kind="miforge_set", miforge_set_kind="corpus")
    other = publish_request_digest(**common, target_kind="miforge_set", miforge_set_kind="test_set")
    assert len({dataset, as_set, other}) == 3
