"""A declared visibility floor (017 FTASKS 10.7; FR-017.48; FTID §3.9, I8; TD-017.31).

Real builds and 008's real publish worker: a recipe declaring ``visibility_floor: private_only``
records it on its version; a child built from that version inherits it (M35); C-9 refuses a PUBLIC
push of either, naming the declaration, and allows a private one; the same rows built without the
declaration publish publicly (M26). A detector-set send naming such a version publicly is refused
before any approval is queued. Every recipe saved without the field keeps its hash.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from src.core.database import sync_session_factory
from src.models import Job
from src.models.approval import Approval
from src.models.version import Version
from src.schemas.recipes import RecipeBody
from src.services.identity import recipe_hash
from src.services.publishing.checks import Outcome, c9_visibility_floor
from tests.support.publish_fixtures import (
    PublishDriver,
    completed_build,
    owners_report_green,
    publish_and_run,
    publisher,
    store_token,
)
from tests.support.stub_operators import body
from tests.support.version_fixtures import HUMOR_TEST, HUMOR_TRAIN, BuildDriver, driver, make_source

__all__ = ["driver", "publisher"]

AGENT = {"X-Dataworks-Agent": "agent:dataworks-mcp"}


async def build(
    client: httpx.AsyncClient,
    driver: BuildDriver,
    name: str,
    inputs: list[dict[str, Any]],
    *,
    floor: bool,
) -> str:
    ds = await client.post("/api/v1/datasets", json={"name": name, "target_type": "detector"})
    assert ds.status_code == 201, ds.text
    recipe = body(("stub_keep", {}))
    if floor:
        recipe["visibility_floor"] = "private_only"
    rec = await client.post("/api/v1/recipes", json={"name": f"{name}-r", "body": recipe})
    assert rec.status_code == 201, rec.text
    response = await client.post(
        "/api/v1/versions",
        json={
            "dataset_id": ds.json()["id"],
            "recipe_revision_id": rec.json()["head_revision_id"],
            "inputs": inputs,
        },
    )
    assert response.status_code == 202, response.text
    assert driver.run(response.json()["job_id"]) == "completed"
    with sync_session_factory()() as db:
        job = db.get(Job, response.json()["job_id"])
        assert job is not None and job.result is not None
        return str(job.result["version_id"])


def floor_of(version_id: str) -> str | None:
    with sync_session_factory()() as db:
        row = db.get(Version, version_id)
        assert row is not None
        return row.visibility_floor


def test_a_recipe_without_the_field_keeps_its_hash() -> None:
    plain = body(("stub_keep", {}))
    parsed = RecipeBody.model_validate(plain).model_dump(mode="json")
    assert parsed == plain and recipe_hash(parsed) == recipe_hash(plain)
    floored = RecipeBody.model_validate({**plain, "visibility_floor": "private_only"})
    assert floored.model_dump(mode="json")["visibility_floor"] == "private_only"
    with pytest.raises(ValueError):
        RecipeBody.model_validate({**plain, "visibility_floor": "public_only"})


def test_c9_refuses_only_a_public_push_of_a_floored_version() -> None:
    from dataclasses import replace

    from tests.unit.publishing.test_checks import green

    base = green()
    assert c9_visibility_floor(replace(base, visibility="public")).outcome is Outcome.GREEN
    floored = replace(base, visibility="public", visibility_floor="private_only")
    refused = c9_visibility_floor(floored)
    assert refused.outcome is Outcome.REFUSED and "private_only" in refused.reason
    private = replace(base, visibility="private", visibility_floor="private_only")
    assert c9_visibility_floor(private).outcome is Outcome.GREEN


async def test_a_public_publish_of_a_floored_version_and_its_child_is_refused(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owners_report_green(monkeypatch)
    await store_token(client)
    rows = {"train": HUMOR_TRAIN, "test": HUMOR_TEST}
    source = make_source(data_dir, rows, repo_id="org/floor")
    floored = await build(
        client, driver, "floored", [{"kind": "source", "source_id": source}], floor=True
    )
    child = await build(
        client, driver, "floored-child", [{"kind": "version", "version_id": floored}], floor=False
    )
    plain = await build(
        client,
        driver,
        "plain",
        [{"kind": "source", "source_id": make_source(data_dir, rows, repo_id="org/plain")}],
        floor=False,
    )
    assert (floor_of(floored), floor_of(child), floor_of(plain)) == (
        "private_only",
        "private_only",
        None,
    )
    for version_id, repo in ((floored, "mistudio/floor-a"), (child, "mistudio/floor-b")):
        b = await completed_build(client, publisher, version_id)
        pub = await publish_and_run(
            client, publisher, version_id, b["id"], repo=repo, visibility="public"
        )
        assert pub["status"] == "refused", pub
        assert pub["error"]["code"] == "private_only_public_refused", pub["error"]
        assert [c["check"] for c in pub["error"]["details"]["checks"]] == ["C-9"]
        private = await publish_and_run(
            client, publisher, version_id, b["id"], repo=repo + "-p", visibility="private"
        )
        assert private["status"] == "published", private["error"]
    b = await completed_build(client, publisher, plain)
    pub = await publish_and_run(
        client, publisher, plain, b["id"], repo="mistudio/plain", visibility="public"
    )
    assert pub["status"] == "published", pub["error"]


async def test_a_public_send_of_a_floored_version_is_refused_before_an_approval(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    data_dir: Path,
) -> None:
    source = make_source(data_dir, {"train": HUMOR_TRAIN, "test": HUMOR_TEST}, repo_id="org/fs")
    floored = await build(
        client, driver, "floored-send", [{"kind": "source", "source_id": source}], floor=True
    )

    def role(kind: str, split: str, **extra: Any) -> dict[str, Any]:
        return {
            "role": kind,
            "version_id": floored,
            "split": split,
            "input_column": "text",
            "label_column": "label",
            "label_mapping": {"1": "positive", "0": "negative"},
            **extra,
        }

    created = await client.post(
        "/api/v1/detector-sets",
        json={
            "name": "floored-set",
            "description": "a set over a private-only version",
            "positive_meaning": "funny",
            "roles": [role("train", "train"), role("id_test", "test")],
        },
    )
    assert created.status_code == 201, created.text
    set_id = created.json()["id"]
    for headers in (AGENT, {}):
        refused = await client.post(
            f"/api/v1/detector-sets/{set_id}/send",
            json={"visibility": "public", "namespace": "org"},
            headers=headers,
        )
        assert refused.status_code == 422, refused.text
        error = refused.json()["error"]
        assert error["code"] == "private_only_public_refused"
        assert error["details"]["version_id"] == floored
    with sync_session_factory()() as db:
        assert db.execute(select(func.count()).select_from(Approval)).scalar_one() == 0


async def test_a_private_push_of_a_floored_version_into_a_public_repository_is_refused(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review R4: an existing PUBLIC repository makes a "private" push public (008's EC-1). C-9
    must judge the push it would make, so a private request into a public repository of a
    ``private_only`` version is refused and nothing is written."""
    owners_report_green(monkeypatch)
    await store_token(client)
    source = make_source(
        data_dir, {"train": HUMOR_TRAIN, "test": HUMOR_TEST}, repo_id="org/floor-ec1"
    )
    floored = await build(
        client, driver, "floored-ec1", [{"kind": "source", "source_id": source}], floor=True
    )
    publisher.hub.add_foreign_file("mistudio/already-public", "README.md")
    publisher.hub.repos["mistudio/already-public"].private = False
    commits = len(publisher.hub.repos["mistudio/already-public"].commits)
    b = await completed_build(client, publisher, floored)
    pub = await publish_and_run(
        client, publisher, floored, b["id"], repo="mistudio/already-public", visibility="private"
    )
    assert pub["status"] == "refused", pub
    assert pub["error"]["code"] == "private_only_public_refused", pub["error"]
    assert len(publisher.hub.repos["mistudio/already-public"].commits) == commits
