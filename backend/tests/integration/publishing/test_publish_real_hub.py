"""A publish to the REAL Hugging Face Hub, through the app's own publish path (008 FTASKS 8.11).

FR-008.3, AC-US1. Env-gated (``tests/support/real_hub.py``): it skips loudly unless
``MIDATAWORKS_REAL_HUB_TOKEN`` and ``MIDATAWORKS_REAL_HUB_NAMESPACE`` are set, and fails under
``MIDATAWORKS_REQUIRE_REAL_HUB=1`` when they are not.

When enabled it builds a 100-row version (train 90, held-out test 10) for real, stores the token
through the Settings API, and runs the publish worker's REAL task bodies with the real
``HubClient`` over the real ``HfApi`` (nothing is faked but Celery's broker). It publishes to a NEW
private repository with a unique name, checks the app's verification and its re-verify job, loads
the splits with ``load_dataset(repo, revision=commit)``, confirms an anonymous read is refused, and
deletes the repository in a ``finally`` block. The repository is proved absent before the publish,
so the delete can only ever remove what this test created.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.support.publish_fixtures import PublishDriver, completed_build, publish_and_run
from tests.support.real_hub import RealHubTarget, real_hub_target
from tests.support.version_fixtures import BuildDriver, driver, make_source

__all__ = ["driver"]

ROWS = 100
HELD_OUT = 10


@pytest.fixture
def real_publisher(
    monkeypatch: pytest.MonkeyPatch, driver: BuildDriver, data_dir: Path
) -> PublishDriver:
    """``publish_fixtures.publisher`` WITHOUT the fake: ``HUB_API_FACTORY`` stays None, so the
    worker's ``HubClient`` builds a real ``HfApi`` from the token it decrypts."""
    from src.core.celery_app import celery_app
    from src.workers import publish_tasks

    assert publish_tasks.HUB_API_FACTORY is None, "the real path must not run on a stand-in"
    drv = PublishDriver(hub=None, builds=driver)  # type: ignore[arg-type]

    def send(name: str, args: list[Any] | None = None, **kwargs: Any) -> Any:
        if name.startswith("midataworks.publish."):
            return drv.send_task(name, args, **kwargs)
        return driver.send_task(name, args, **kwargs)

    monkeypatch.setattr(celery_app, "send_task", send)
    monkeypatch.setattr(publish_tasks, "emit", drv.emit)
    monkeypatch.setattr(publish_tasks, "_next_jobs", lambda: None)
    return drv


async def _version_of_100_rows(
    client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path
) -> str:
    from src.core.database import sync_session_factory
    from src.models import Job
    from tests.support.stub_operators import body

    rows = [
        {"text": f"Real-Hub check sentence number {i}.", "label": i % 2, "score": i / ROWS}
        for i in range(ROWS)
    ]
    source_id = make_source(data_dir, {"train": rows}, repo_id="org/real-hub-8-11")
    ds = await client.post(
        "/api/v1/datasets", json={"name": "real-hub-8-11", "target_type": "detector"}
    )
    assert ds.status_code == 201, ds.text
    recipe = body(("stub_split", {"held_out": "test", "count": HELD_OUT}))
    rec = await client.post("/api/v1/recipes", json={"name": "real-hub-8-11-r", "body": recipe})
    assert rec.status_code == 201, rec.text
    response = await client.post(
        "/api/v1/versions",
        json={
            "dataset_id": ds.json()["id"],
            "recipe_revision_id": rec.json()["head_revision_id"],
            "inputs": [{"kind": "source", "source_id": source_id}],
        },
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    assert driver.run(job_id) == "completed"
    with sync_session_factory()() as db:
        job = db.get(Job, job_id)
        assert job is not None and job.result is not None
        return str(job.result["version_id"])


async def test_a_private_publish_to_the_real_hub_verifies_loads_and_is_deleted(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    real_publisher: PublishDriver,
    data_dir: Path,
    tmp_path: Path,
) -> None:
    # first, so a missing token is a FAILED test under REQUIRE (not a fixture error) or a skip
    target: RealHubTarget = real_hub_target()
    from datasets import load_dataset
    from huggingface_hub import HfApi

    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    repo = f"{target.namespace}/dw-it-8-11-{stamp}-{uuid.uuid4().hex[:8]}"
    api = HfApi(token=target.token)
    assert not api.repo_exists(repo, repo_type="dataset"), f"{repo} already exists; not touching it"

    stored = await client.put("/api/v1/settings/hf_token", json={"value": target.token})
    assert stored.status_code == 200, "storing the token through Settings failed"
    version_id = await _version_of_100_rows(client, driver, data_dir)
    build = await completed_build(client, real_publisher, version_id)
    assert {f["name"]: f["rows"] for f in build["files"]} == {
        "train": ROWS - HELD_OUT,
        "test": HELD_OUT,
    }

    try:
        pub = await publish_and_run(
            client, real_publisher, version_id, build["id"], repo=repo, visibility="private"
        )
        # the app's own verification, recorded on the publish
        assert pub["status"] == "published", pub["error"]
        assert pub["visibility_after"] == "private"
        commit = pub["commit"]
        assert commit
        assert pub["files"] and all(f["match"] is True for f in pub["files"]), pub["files"]
        assert {f["path"] for f in pub["files"]} >= {f["path"] for f in build["files"]} | {
            "README.md",
            "midataworks-dataset-version.json",
        }

        # the app's re-verify job against the Hub as it now stands
        response = await client.post(f"/api/v1/publishes/{pub['id']}/reverify")
        assert response.status_code == 202, response.text
        real_publisher.run_publish_tasks()
        job = real_publisher.job(response.json()["job_id"])
        assert job.status == "completed", job.error
        assert job.result is not None
        assert job.result["status"] == "hashes_match" and job.result["head_moved"] is False

        # the Hub's own view: private, at the published commit
        info = api.dataset_info(repo)
        assert info.private is True and info.sha == commit

        # FR-008.3: the splits load at the commit with the built counts
        loaded = load_dataset(
            repo, revision=commit, token=target.token, cache_dir=str(tmp_path / "hf-cache")
        )
        assert {name: split.num_rows for name, split in loaded.items()} == {
            "train": ROWS - HELD_OUT,
            "test": HELD_OUT,
        }
        assert "text" in loaded["train"].column_names

        # an anonymous read is refused: the API and a raw file
        for url in (
            f"https://huggingface.co/api/datasets/{repo}",
            f"https://huggingface.co/datasets/{repo}/resolve/{commit}/README.md",
        ):
            anonymous = httpx.get(url, follow_redirects=False, timeout=30)
            assert anonymous.status_code in (401, 403, 404), (url, anonymous.status_code)
    finally:
        api.delete_repo(repo, repo_type="dataset", missing_ok=True)
    assert not api.repo_exists(repo, repo_type="dataset"), f"{repo} survived its delete"
