"""Session data: the operator's mark, the lineage caveat, the preview warning (015 FR-015.12 -
FR-015.14; ENH-005 D38 warn only, D56).

Two versions are built from two sources holding the same rows; only one source is marked. Every
manifest of the marked one carries the amber ``session_data`` caveat (draft, publish, export), the
unmarked twin's carry none, the publish preview warns before a public push, and the public push is
still ALLOWED (D38: no publishing check is added).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest

from src.core.database import sync_session_factory
from src.models import Job, Source
from tests.support.publish_fixtures import (
    PublishDriver,
    completed_build,
    publish_and_run,
    publisher,
    store_token,
)
from tests.support.stub_operators import body
from tests.support.version_fixtures import (
    HUMOR_TEST,
    HUMOR_TRAIN,
    BuildDriver,
    driver,
    make_source,
)

__all__ = ["driver", "publisher"]

REPO_A = "mistudio/session-a"


async def _version_from(
    client: httpx.AsyncClient, driver: BuildDriver, source_id: str, name: str
) -> str:
    ds = await client.post("/api/v1/datasets", json={"name": name, "target_type": "detector"})
    assert ds.status_code == 201, ds.text
    rec = await client.post(
        "/api/v1/recipes", json={"name": f"{name}-r", "body": body(("stub_keep", {}))}
    )
    response = await client.post(
        "/api/v1/versions",
        json={
            "dataset_id": ds.json()["id"],
            "recipe_revision_id": rec.json()["head_revision_id"],
            "inputs": [{"kind": "source", "source_id": source_id}],
        },
    )
    assert response.status_code == 202, response.text
    assert driver.run(response.json()["job_id"]) == "completed"
    with sync_session_factory()() as db:
        job = db.get(Job, response.json()["job_id"])
        assert job is not None and job.result is not None
        return str(job.result["version_id"])


def _session_codes(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return [c for c in manifest["caveats"] if c["code"] == "session_data"]


async def _mark(client: httpx.AsyncClient, source_id: str, flag: bool) -> httpx.Response:
    return await client.post(
        f"/api/v1/sources/{source_id}/session-data", json={"session_data": flag}
    )


async def test_a_marked_sources_versions_carry_the_caveat_and_warn_but_never_block(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.support.publish_fixtures import owners_report_green

    owners_report_green(monkeypatch)
    await store_token(client)
    rows = {
        split: [{**r, "session_id": f"s{i % 3}"} for i, r in enumerate(data)]
        for split, data in (("train", HUMOR_TRAIN), ("test", HUMOR_TEST))
    }
    marked = make_source(data_dir, rows, repo_id="org/session-a")
    twin = make_source(data_dir, rows, repo_id="org/session-b")
    res = await _mark(client, marked, True)
    assert res.status_code == 200, res.text
    assert res.json()["session_data"] is True and res.json()["session_data_basis"] == "operator"

    v_marked = await _version_from(client, driver, marked, "session-a")
    v_twin = await _version_from(client, driver, twin, "session-b")
    b_marked = await completed_build(client, publisher, v_marked)
    b_twin = await completed_build(client, publisher, v_twin)

    # the draft manifest (card draft) — from lineage, though the rows hold no session column
    draft = (
        await client.get(
            f"/api/v1/versions/{v_marked}/card-draft", params={"build_id": b_marked["id"]}
        )
    ).json()
    assert "session_data" in draft["record_markdown"]
    draft_twin = (
        await client.get(f"/api/v1/versions/{v_twin}/card-draft", params={"build_id": b_twin["id"]})
    ).json()
    assert "session_data" not in draft_twin["record_markdown"]

    # the preview warns before a PUBLIC push only
    async def preview(version_id: str, build_id: str, visibility: str) -> dict[str, Any]:
        started = await client.post(
            f"/api/v1/versions/{version_id}/publish-checks",
            json={"build_id": build_id, "repo_id": REPO_A, "visibility": visibility},
        )
        assert started.status_code == 202, started.text
        publisher.run_publish_tasks()
        got = await client.get(f"/api/v1/publish-check-runs/{started.json()['check_run_id']}")
        return dict(got.json())

    public = await preview(v_marked, b_marked["id"], "public")
    [warning] = public["warnings"]
    assert warning["code"] == "session_data" and warning["sources"][0]["source_id"] == marked
    assert (await preview(v_marked, b_marked["id"], "private"))["warnings"] == []
    assert (await preview(v_twin, b_twin["id"], "public"))["warnings"] == []
    # D38: no check entry is added
    assert not any("session" in str(r["check"]) for r in public["results"])

    # the public push is allowed, and the published manifest and card carry the caveat
    pub = await publish_and_run(
        client, publisher, v_marked, b_marked["id"], repo=REPO_A, visibility="public"
    )
    assert pub["status"] == "published", pub["error"]
    assert pub["visibility_after"] == "public"
    files = publisher.hub.head_files(REPO_A)
    manifest = json.loads(files["midataworks-dataset-version.json"])
    [caveat] = _session_codes(manifest)
    assert caveat["severity"] == "amber" and caveat["detail"]["sources"][0]["source_id"] == marked
    assert "(session_data)" in files["README.md"].decode()
    # FR-015.14: the session id reads ``group`` in a session-data version only
    semantic = {c["name"]: c["semantic"] for c in manifest["content"]["columns"]}
    assert semantic["session_id"] == "group"

    # the unmarked twin's publish carries none
    pub_twin = await publish_and_run(
        client, publisher, v_twin, b_twin["id"], repo="mistudio/session-b"
    )
    assert pub_twin["status"] == "published", pub_twin["error"]
    twin_manifest = json.loads(
        publisher.hub.head_files("mistudio/session-b")["midataworks-dataset-version.json"]
    )
    assert _session_codes(twin_manifest) == []
    twin_semantic = {c["name"]: c["semantic"] for c in twin_manifest["content"]["columns"]}
    assert twin_semantic["session_id"] == "other"


async def test_the_mark_clears_only_while_nothing_derives(
    client: httpx.AsyncClient, operator_name: str, driver: BuildDriver, data_dir: Path
) -> None:
    source = make_source(data_dir, {"train": HUMOR_TRAIN}, repo_id="org/session-c")
    assert (await _mark(client, source, True)).status_code == 200
    cleared = await _mark(client, source, False)
    assert cleared.status_code == 200 and cleared.json()["session_data"] is False
    assert (await _mark(client, source, True)).status_code == 200
    await _version_from(client, driver, source, "session-c")
    refused = await _mark(client, source, False)
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "source_has_versions"
    with sync_session_factory()() as db:
        row = db.get(Source, source)
        assert row is not None and row.session_data is True


async def test_an_agent_cannot_mark(client: httpx.AsyncClient, data_dir: Path) -> None:
    source = make_source(data_dir, {"train": HUMOR_TRAIN}, repo_id="org/session-d")
    res = await client.post(
        f"/api/v1/sources/{source}/session-data",
        json={"session_data": True},
        headers={"X-Dataworks-Agent": "agent:claude"},
    )
    assert res.status_code == 403 and res.json()["error"]["code"] == "agent_forbidden"


async def test_a_recognised_source_cannot_be_cleared(
    client: httpx.AsyncClient, data_dir: Path
) -> None:
    source = make_source(data_dir, {"train": HUMOR_TRAIN}, repo_id="org/session-e")
    with sync_session_factory()() as db:
        row = db.get(Source, source)
        assert row is not None
        row.session_data, row.session_data_basis = True, "recognised"
        db.commit()
    res = await _mark(client, source, False)
    assert res.status_code == 409 and res.json()["error"]["code"] == "session_data_recognised"


def test_commit_ready_sets_the_flag_from_the_stored_detection(data_dir: Path) -> None:
    """The one setter for a recognised export (both import paths reach ``commit_ready``)."""
    import uuid

    from src.services.sources import source_service

    with sync_session_factory()() as db:
        for provenance, expected in (("mistudio_spot_check/v1", True), (None, False)):
            row = Source(
                id=str(uuid.uuid4()),
                kind="upload",
                state="importing",
                display_name="disagreements.jsonl",
                licence_display="not stated",
                library_versions={},
                created_by="Test Operator",
                created_by_origin="operator",
                content_hash=uuid.uuid4().hex * 2,
            )
            db.add(row)
            db.commit()
            source_service.commit_ready(db, row, [], {"provenance": provenance})
            db.refresh(row)
            assert row.session_data is expected
            assert row.session_data_basis == ("recognised" if expected else None)


def test_session_text_is_never_logged(
    caplog: pytest.LogCaptureFixture, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FTDD 015 section 8: a sentinel from the rows appears in no log record during detection or
    D-9 (the publish path logs no row text either; covered by the publish test above)."""
    from src.services.detector_sets import separation_wording
    from src.services.sources.detection import detect

    sentinel = "SENTINEL-7f3c-private-text"
    caplog.set_level(logging.DEBUG)
    rows = [
        {
            "messages": [{"role": "user", "content": f"{sentinel} {i} hello there"}],
            "session_id": f"s{i}",
        }
        for i in range(50)
    ]
    detect([("messages", "list"), ("session_id", "string")], rows, spot_columns=("session_id",))
    texts = [f"{sentinel} {'zebra' if i % 2 else 'yak'} w{i}" for i in range(60)]
    separation_wording.wording_auroc(texts, [i % 2 for i in range(60)])
    assert all(sentinel not in r.getMessage() for r in caplog.records)
