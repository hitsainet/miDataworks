"""A sensitive version is private only, and says so; every surface reads the live value (021
FTASKS 8.8, 13.x; FR-021.20, FR-021.23; suite P42).

Real builds over the stub operators, the publish worker's real task bodies on the fake Hub, the
real export and reward bundle, and the operator's session-data mark through its route.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from src.core.database import sync_session_factory
from src.core.storage import export_dir
from src.models.approval import Approval
from src.services.publishing.manifest_builder import MANIFEST_FILE
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

ROWS = {"train": HUMOR_TRAIN, "test": HUMOR_TEST}
AGENT = {"X-Dataworks-Agent": "agent:mcp"}


async def build(
    client: httpx.AsyncClient,
    driver: BuildDriver,
    name: str,
    source: str,
    declared: str | None,
) -> str:
    from src.core.database import sync_session_factory as factory
    from src.models import Job

    ds = await client.post("/api/v1/datasets", json={"name": name, "target_type": "detector"})
    recipe = body(("stub_split", {"held_out": "test", "count": 3}))
    if declared is not None:
        recipe["sensitivity"] = declared
    rec = await client.post("/api/v1/recipes", json={"name": f"{name}-r", "body": recipe})
    assert ds.status_code == 201 and rec.status_code == 201, (ds.text, rec.text)
    response = await client.post(
        "/api/v1/versions",
        json={
            "dataset_id": ds.json()["id"],
            "recipe_revision_id": rec.json()["head_revision_id"],
            "inputs": [{"kind": "source", "source_id": source}],
        },
    )
    assert response.status_code == 202, response.text
    assert driver.run(response.json()["job_id"]) == "completed"
    with factory()() as db:
        job = db.get(Job, response.json()["job_id"])
        assert job is not None and job.result is not None
        return str(job.result["version_id"])


async def test_sensitive_public_refused_and_private_labelled(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-021.23: a public push of a sensitive version is refused by name (C-9); a private push
    succeeds and its card and checks say "sensitive"; a not-sensitive version publishes publicly."""
    owners_report_green(monkeypatch)
    await store_token(client)
    hot = await build(
        client, driver, "hot", make_source(data_dir, ROWS, repo_id="o/hot"), "sensitive"
    )
    b = await completed_build(client, publisher, hot)
    pub = await publish_and_run(
        client, publisher, hot, b["id"], repo="mistudio/hot", visibility="public"
    )
    assert pub["status"] == "refused", pub
    assert pub["error"]["code"] == "sensitive_public_refused", pub["error"]
    private = await publish_and_run(
        client, publisher, hot, b["id"], repo="mistudio/hot-p", visibility="private"
    )
    assert private["status"] == "published", private["error"]
    files = publisher.hub.head_files("mistudio/hot-p")
    assert "Sensitivity: sensitive — private only." in files["README.md"].decode()
    # R2 review fix (OR2-021-5), 2026-11-16: a private push of a sensitive version reads the
    # implied private_only floor (sensitivity.implied_floor, the only copy); the stored floor
    # is not declared.
    started = await client.post(
        f"/api/v1/versions/{hot}/publish-checks",
        json={"build_id": b["id"], "repo_id": "mistudio/hot-p2", "visibility": "private"},
    )
    assert started.status_code == 202, started.text
    publisher.run_publish_tasks()
    checks = (
        await client.get(f"/api/v1/publish-check-runs/{started.json()['check_run_id']}")
    ).json()
    c9 = {r["check"]: r for r in checks["results"]}["C-9"]
    assert c9["outcome"] == "green", c9
    assert c9["evidence"] == {"visibility_floor": "private_only", "sensitivity": "sensitive"}
    manifest = json.loads(files[MANIFEST_FILE])
    assert manifest["sensitivity"] == "sensitive"
    cold = await build(
        client, driver, "cold", make_source(data_dir, ROWS, repo_id="o/cold"), "not_sensitive"
    )
    b = await completed_build(client, publisher, cold)
    pub = await publish_and_run(
        client, publisher, cold, b["id"], repo="mistudio/cold", visibility="public"
    )
    assert pub["status"] == "published", pub["error"]
    assert pub["visibility_after"] == "public"


async def test_sensitive_public_send_refused(
    client: httpx.AsyncClient, operator_name: str, driver: BuildDriver, data_dir: Path
) -> None:
    """FR-021.23 (M-44): a public send of a set binding a sensitive version is refused up front,
    before any approval is queued, by operator and agent alike."""
    hot = await build(
        client, driver, "hot-send", make_source(data_dir, ROWS, repo_id="o/hs"), "sensitive"
    )

    def role(kind: str, split: str) -> dict[str, Any]:
        return {
            "role": kind,
            "version_id": hot,
            "split": split,
            "input_column": "text",
            "label_column": "label",
            "label_mapping": {"1": "positive", "0": "negative"},
        }

    created = await client.post(
        "/api/v1/detector-sets",
        json={
            "name": "hot-set",
            "description": "a set over a sensitive version",
            "positive_meaning": "funny",
            "roles": [role("train", "train"), role("id_test", "test")],
        },
    )
    assert created.status_code == 201, created.text
    for headers in (AGENT, {}):
        refused = await client.post(
            f"/api/v1/detector-sets/{created.json()['id']}/send",
            json={"visibility": "public", "namespace": "org"},
            headers=headers,
        )
        assert refused.status_code == 422, refused.text
        assert refused.json()["error"]["code"] == "sensitive_public_refused"
        assert refused.json()["error"]["details"]["version_id"] == hot
    with sync_session_factory()() as db:
        assert db.execute(select(func.count()).select_from(Approval)).scalar_one() == 0


REWARD_RUBRIC = {
    "style": "binary",
    "messages": [
        {"role": "user", "content": "Text: {text}\nAnswer: {completion}\nVERDICT: yes or no."}
    ],
    "input_fields": ["text", "completion"],
    "parser": "verdict_line_v1",
    "allowed_verdicts": ["yes", "no"],
}


async def test_sensitivity_every_surface(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-021.20 (R1A-D7; M-24, M-25, M-40): a version with a NULL floor whose source is marked
    session data AFTER the build reads ``sensitive`` on every surface: the version read, the
    partitions route, the four manifest sites, S-1, C-9 and the card. One surface that read the
    stored column would say null here."""
    owners_report_green(monkeypatch)
    await store_token(client)
    source = make_source(data_dir, ROWS, repo_id="o/later")
    version_id = await build(client, driver, "later", source, None)
    b = await completed_build(client, publisher, version_id)
    before = (await client.get(f"/api/v1/versions/{version_id}")).json()
    assert before["sensitivity"] is None
    published = await publish_and_run(
        client, publisher, version_id, b["id"], repo="mistudio/later", visibility="private"
    )
    assert published["status"] == "published", published["error"]
    marked = await client.post(
        f"/api/v1/sources/{source}/session-data", json={"session_data": True}
    )
    assert marked.status_code == 200, marked.text

    read = (await client.get(f"/api/v1/versions/{version_id}")).json()
    assert read["sensitivity"] == "sensitive"
    assert read["sensitivity_basis"]["floor"] is None
    assert read["sensitivity_basis"]["raised_by"] == [
        {"kind": "session_data_source", "source_id": source}
    ]
    assert read["sensitivity_published_below"] == [published["id"]]
    parts = (await client.get(f"/api/v1/versions/{version_id}/wsp-partitions")).json()
    assert parts["sensitivity"] == "sensitive"

    surfaces: dict[str, Any] = {}
    # the draft: a NEW build's handoff manifest (the stored publish is a snapshot)
    b2 = await completed_build(client, publisher, version_id)
    draft = await client.get(
        f"/api/v1/versions/{version_id}/card-draft", params={"build_id": b2["id"]}
    )
    assert draft.status_code == 200, draft.text
    assert "Sensitivity: sensitive — private only." in draft.json()["record_markdown"]
    pub = await publish_and_run(
        client, publisher, version_id, b2["id"], repo="mistudio/later-2", visibility="private"
    )
    assert pub["status"] == "published", pub["error"]
    surfaces["publish"] = json.loads(publisher.hub.head_files("mistudio/later-2")[MANIFEST_FILE])
    exported = await client.post(
        "/api/v1/exports",
        json={
            "target": "miforge_set",
            "miforge_set_kind": "corpus",
            "version_id": version_id,
            "label_column": "label",
        },
    )
    assert exported.status_code == 202, exported.text
    publisher.run_publish_tasks()
    row = (await client.get(f"/api/v1/exports/{exported.json()['export_id']}")).json()
    assert row["status"] == "completed", row
    surfaces["export"] = json.loads((export_dir(row["id"]) / MANIFEST_FILE).read_bytes())
    rubric = await client.post(
        "/api/v1/rubrics", json={"name": "later/judge", "body": REWARD_RUBRIC}
    )
    assert rubric.status_code == 201, rubric.text
    bundled = await client.post(
        "/api/v1/exports",
        json={
            "target": "reward_bundle",
            "version_id": version_id,
            "rubric_id": rubric.json()["id"],
            "judge_model_id": "judge-7b",
            "verdict_scores": {"yes": 1.0, "no": 0.0},
            "seed": 11,
        },
    )
    assert bundled.status_code == 202, bundled.text
    publisher.run_publish_tasks()
    row = (await client.get(f"/api/v1/exports/{bundled.json()['export_id']}")).json()
    assert row["status"] == "completed", row
    surfaces["reward_bundle"] = json.loads((export_dir(row["id"]) / MANIFEST_FILE).read_bytes())
    # the draft site serves the newest published manifest once one exists; its document was built
    # by drafts.built_document for the card draft above (the record line), and by run_publish
    for site, manifest in surfaces.items():
        assert manifest["sensitivity"] == "sensitive", site
        assert manifest["extensions"]["sensitivity_basis"]["raised_by"][0]["source_id"] == source
    # S-1 and C-9 through the checks preview
    started = await client.post(
        f"/api/v1/versions/{version_id}/publish-checks",
        json={"build_id": b2["id"], "repo_id": "mistudio/later-3", "visibility": "public"},
    )
    assert started.status_code == 202, started.text
    publisher.run_publish_tasks()
    checks = (
        await client.get(f"/api/v1/publish-check-runs/{started.json()['check_run_id']}")
    ).json()
    by = {r["check"]: r for r in checks["results"]}
    assert by["S-1"]["outcome"] == "green" and "sensitive" in by["S-1"]["reason"]
    assert by["C-9"]["outcome"] == "refused"
    assert by["C-9"]["evidence"]["code"] == "sensitive_public_refused"
    # and the public push itself is refused (M-40: C-9 reading only the stored floor)
    refused = await publish_and_run(
        client, publisher, version_id, b2["id"], repo="mistudio/later-4", visibility="public"
    )
    assert refused["status"] == "refused" and refused["error"]["code"] == "sensitive_public_refused"


async def test_the_draft_manifest_reads_the_live_value(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    """The fourth site, ``drafts.built_document`` (served as the handoff manifest before any
    publish), reads the raised value too."""
    source = make_source(data_dir, ROWS, repo_id="o/draft")
    version_id = await build(client, driver, "draft", source, None)
    await completed_build(client, publisher, version_id)
    before = (await client.get(f"/api/v1/versions/{version_id}/handoff-manifest")).json()
    assert before["sensitivity"] is None
    await client.post(f"/api/v1/sources/{source}/session-data", json={"session_data": True})
    after = (await client.get(f"/api/v1/versions/{version_id}/handoff-manifest")).json()
    assert after["sensitivity"] == "sensitive"


async def test_a_version_raised_later_is_refused_a_public_send(
    client: httpx.AsyncClient, operator_name: str, driver: BuildDriver, data_dir: Path
) -> None:
    """R3 review fix (OR3-021-3), 2026-11-16: the send reads the LIVE sensitivity. An undeclared
    version whose source becomes session data after the build is refused a public send, as agent
    and as operator, before any approval is queued."""
    from sqlalchemy import func, select

    from src.models.approval import Approval

    src = make_source(data_dir, ROWS, repo_id="o/raised-send")
    version_id = await build(client, driver, "raised-send", src, None)
    marked = await client.post(f"/api/v1/sources/{src}/session-data", json={"session_data": True})
    assert marked.status_code == 200, marked.text

    def role(kind: str, split: str) -> dict[str, Any]:
        return {
            "role": kind,
            "version_id": version_id,
            "split": split,
            "input_column": "text",
            "label_column": "label",
            "label_mapping": {"1": "positive", "0": "negative"},
        }

    created = await client.post(
        "/api/v1/detector-sets",
        json={
            "name": "raised-set",
            "description": "x",
            "positive_meaning": "funny",
            "roles": [role("train", "train"), role("id_test", "test")],
        },
    )
    assert created.status_code == 201, created.text
    for headers in (AGENT, {}):
        sent = await client.post(
            f"/api/v1/detector-sets/{created.json()['id']}/send",
            json={"visibility": "public", "namespace": "org"},
            headers=headers,
        )
        assert sent.status_code == 422, sent.text
        assert sent.json()["error"]["code"] == "sensitive_public_refused"
    with sync_session_factory()() as db:
        assert db.execute(select(func.count()).select_from(Approval)).scalar_one() == 0
