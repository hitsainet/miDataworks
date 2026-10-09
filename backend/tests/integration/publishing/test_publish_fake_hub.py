"""A publish end to end on the faithful fake Hub (008 FTASKS 8.5–8.12; AC-US1).

Real version build, real worker task bodies, real HubClient; only ``HfApi`` is the fake. The
fake computes every hash from the bytes it received, so "hashes match" here means the files on
the fake Hub are the files built.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import httpx
import pyarrow.parquet as pq

from src.core.database import sync_session_factory
from src.models import Publish
from src.services.publishing.card import read_front_matter
from tests.support.publish_fixtures import (
    REPO,
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


async def _ready(
    client: httpx.AsyncClient, driver: BuildDriver, publisher: PublishDriver, data_dir: Path
) -> tuple[str, dict]:
    await store_token(client)
    version_id = await built_version(client, driver, data_dir)
    build = await completed_build(client, publisher, version_id)
    return version_id, build


async def test_a_private_publish_lands_verified_and_loadable(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    version_id, build = await _ready(client, driver, publisher, data_dir)
    pub = await publish_and_run(client, publisher, version_id, build["id"])
    assert pub["status"] == "published", pub["error"]
    assert pub["visibility_after"] == "private"
    assert publisher.hub.repos[REPO].private is True
    files = publisher.hub.head_files(REPO)
    assert set(files) == {".gitattributes", "README.md", "midataworks-dataset-version.json"} | {
        f["path"] for f in build["files"]
    }
    # every recorded remote hash equals the bytes the fake received, and matches the build
    for f in pub["files"]:
        content = files[f["path"]]
        assert f["match"] is True
        assert f["sha256"] == hashlib.sha256(content).hexdigest()
    # the split files load and have the built row counts
    for f in build["files"]:
        table = pq.read_table(io.BytesIO(files[f["path"]]))
        assert table.num_rows == f["rows"]
        assert {"_dw_row_key", "_dw_occurrence", "_dw_origin"} <= set(table.column_names)
        assert "_dw_split" not in table.column_names
    # the card's configs block maps each split to its file
    fm = read_front_matter(files["README.md"])
    assert {d["split"]: d["path"] for d in fm["configs"][0]["data_files"]} == {
        f["name"]: f["path"] for f in build["files"]
    }
    # the in-repository manifest is valid and in_repository; the record holds the published one
    manifest = json.loads(files["midataworks-dataset-version.json"])
    assert manifest["publication"] == {
        "state": "in_repository",
        "repo_id": REPO,
        "repo_type": "dataset",
    }
    served = (await client.get(f"/api/v1/versions/{version_id}/handoff-manifest")).json()
    assert served["publication"]["state"] == "published"
    assert served["publication"]["commit"] == pub["commit"]
    assert served["publication"]["verification"]["files_checked"] == len(pub["files"])
    # the job is completed and the operator recorded as who
    assert pub["started_by"] == operator_name and pub["started_by_origin"] == "operator"


async def test_omitted_visibility_is_private(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    version_id, build = await _ready(client, driver, publisher, data_dir)
    response = await publish(client, publisher, version_id, build["id"], visibility=None)
    assert response.status_code == 201
    with sync_session_factory()() as db:
        row = db.get(Publish, response.json()["publish_id"])
        assert row is not None and row.requested_visibility == "private"


# --- public pushes and the amber rule (X-02, P-01, P-02) -------------------------------------


async def test_a_public_push_is_refused_while_any_check_is_not_checked(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    """004 and 006 are both built (2026-10-07). 006: no audit was drawn, so C-5 is checked and
    amber with state "none". 004 answers for real on this 13-row version, whose build seed varies per
    run: C-7 is amber (too few rows for a held-out audit, or a column clearing the margin by chance)
    or green, and C-3 depends on whether the seeded split separates HUMOR_TRAIN's one duplicate. None
    of them is not_checked any more; C-5 alone already refuses a public push."""
    version_id, build = await _ready(client, driver, publisher, data_dir)
    pub = await publish_and_run(client, publisher, version_id, build["id"], visibility="public")
    assert pub["status"] == "refused"
    assert pub["error"]["code"] == "publish_refused_amber"
    checks = {c["check"]: c for c in pub["error"]["details"]["checks"]}
    assert {"C-5"} <= set(checks) <= {"C-3", "C-5", "C-7"}
    assert checks["C-5"]["evidence"] == {"state": "none"}
    for check in ("C-3", "C-7"):
        if check in checks:
            assert not checks[check]["evidence"].get("not_checked"), check
    assert REPO not in publisher.hub.repos, "nothing may reach the Hub"
    assert not [c for c in publisher.hub.calls if c[0] in ("create_repo", "create_commit")]


async def test_the_same_version_publishes_privately_with_the_amber_caveats(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    version_id, build = await _ready(client, driver, publisher, data_dir)
    pub = await publish_and_run(client, publisher, version_id, build["id"], visibility="private")
    assert pub["status"] == "published"
    manifest = json.loads(publisher.hub.head_files(REPO)["midataworks-dataset-version.json"])
    codes = {(c["code"], c["detail"]["check"]) for c in manifest["caveats"]}
    assert ("amber_check", "C-5") in codes
    # 004 is real (2026-10-07): C-3 and C-7 appear only when the seeded build gives them a finding
    assert {c for _, c in codes} <= {"C-3", "C-5", "C-7"}


async def test_when_the_owners_report_clean_a_public_push_flips_after_verification(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
    monkeypatch,
) -> None:
    from tests.support.publish_fixtures import owners_report_green

    owners_report_green(monkeypatch)
    version_id, build = await _ready(client, driver, publisher, data_dir)
    pub = await publish_and_run(client, publisher, version_id, build["id"], visibility="public")
    assert pub["status"] == "published", pub["error"]
    assert pub["visibility_after"] == "public"
    assert publisher.hub.repos[REPO].private is False
    names = [c[0] for c in publisher.hub.calls]
    create = names.index("create_repo")
    assert publisher.hub.calls[create][1]["private"] is True, "created private first"
    flip = names.index("update_repo_settings")
    verify = max(i for i, n in enumerate(names[:flip]) if n == "dataset_info")
    assert names.index("create_commit") < verify < flip, "flip only after verification"


async def test_an_unlicensed_source_refuses_public_and_allows_private(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
    monkeypatch,
) -> None:
    from tests.support.publish_fixtures import owners_report_green

    owners_report_green(monkeypatch)
    await store_token(client)
    version_id = await built_version(client, driver, data_dir, licence=None)
    build = await completed_build(client, publisher, version_id)
    pub = await publish_and_run(client, publisher, version_id, build["id"], visibility="public")
    assert pub["status"] == "refused"
    assert [c["check"] for c in pub["error"]["details"]["checks"]] == ["C-1"]
    pub = await publish_and_run(client, publisher, version_id, build["id"], visibility="private")
    assert pub["status"] == "published"


# --- edge cases ------------------------------------------------------------------------------


async def test_ec1_an_existing_public_repository_makes_the_push_public(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    from tests.support.fake_hub import GITATTRIBUTES, Repo

    repo = Repo(private=False)
    publisher.hub.repos[REPO] = repo
    publisher.hub._append(repo, {".gitattributes": GITATTRIBUTES}, "initial")
    version_id, build = await _ready(client, driver, publisher, data_dir)
    pub = await publish_and_run(client, publisher, version_id, build["id"], visibility="private")
    assert pub["status"] == "refused", "public checks apply: 004/006 not_checked are amber"
    assert pub["error"]["details"]["repository_public"] is True
    assert publisher.hub.commit_count(REPO) == 1


async def test_ec2_a_private_repository_goes_public_only_through_the_checks(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
    monkeypatch,
) -> None:
    from tests.support.publish_fixtures import owners_report_green

    version_id, build = await _ready(client, driver, publisher, data_dir)
    first = await publish_and_run(client, publisher, version_id, build["id"])
    assert first["visibility_after"] == "private"
    owners_report_green(monkeypatch)
    second = await publish_and_run(
        client, publisher, version_id, build["id"], visibility="public", prose="# Humor v2"
    )
    assert second["status"] == "published" and second["visibility_after"] == "public"


async def test_ec3_a_tampered_hash_fails_verification_and_stays_private(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
    monkeypatch,
) -> None:
    from tests.support.publish_fixtures import owners_report_green

    owners_report_green(monkeypatch)
    version_id, build = await _ready(client, driver, publisher, data_dir)
    publisher.hub.corrupt_path = build["files"][0]["path"]
    pub = await publish_and_run(client, publisher, version_id, build["id"], visibility="public")
    assert pub["status"] == "verification_failed"
    assert publisher.hub.repos[REPO].private is True
    assert pub["commit"] is not None, "the commit stays on the Hub and on the record"
    bad = [f for f in pub["files"] if not f["match"]]
    assert [f["path"] for f in bad] == [build["files"][0]["path"]]
    assert bad[0]["remote_lfs_sha256"] == "0" * 64 and bad[0]["sha256"] != "0" * 64
    served = await client.get(f"/api/v1/versions/{version_id}/handoff-manifest")
    assert served.json()["publication"] is None, "the version is not published"


async def test_ec3_a_tampered_small_file_fails_too(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    version_id, build = await _ready(client, driver, publisher, data_dir)
    publisher.hub.corrupt_path = "midataworks-dataset-version.json"
    pub = await publish_and_run(client, publisher, version_id, build["id"])
    assert pub["status"] == "verification_failed"
    assert [f["path"] for f in pub["files"] if not f["match"]] == [
        "midataworks-dataset-version.json"
    ]


async def test_a_file_missing_from_the_commit_fails(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    version_id, build = await _ready(client, driver, publisher, data_dir)
    publisher.hub.drop_path = "README.md"
    pub = await publish_and_run(client, publisher, version_id, build["id"])
    assert pub["status"] == "verification_failed"
    assert pub["error"]["details"]["missing"] == ["README.md"]


async def test_ec4_a_lost_response_is_recovered_without_a_second_commit(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    version_id, build = await _ready(client, driver, publisher, data_dir)
    publisher.hub.lose_next_commit_response = True
    pub = await publish_and_run(client, publisher, version_id, build["id"])
    assert pub["status"] == "published", pub["error"]
    commits = [c for c in publisher.hub.calls if c[0] == "create_commit"]
    assert len(commits) == 1, "the landed commit was found by its marker, not pushed again"
    assert pub["commit"] == publisher.hub.repos[REPO].head.oid
    assert f"[dw-publish {pub['id']}]" in publisher.hub.repos[REPO].head.title


async def test_ec5_the_hub_answering_429_is_retried(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    version_id, build = await _ready(client, driver, publisher, data_dir)
    publisher.hub.fail_reads = 2
    pub = await publish_and_run(client, publisher, version_id, build["id"])
    assert pub["status"] == "published", pub["error"]
    assert publisher.hub.fail_reads == 0


async def test_ec6_no_token_refuses_every_push(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    version_id = await built_version(client, driver, data_dir)
    build = await completed_build(client, publisher, version_id)
    pub = await publish_and_run(client, publisher, version_id, build["id"])
    assert pub["status"] == "refused" and pub["error"]["code"] == "token_cannot_write"
    assert "Settings" in pub["error"]["message"]
    assert not publisher.hub.calls


async def test_ec6_a_read_only_or_revoked_token_refuses_private_too(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    version_id = await built_version(client, driver, data_dir)
    build = await completed_build(client, publisher, version_id)
    publisher.hub.add_token(
        "hf_readonly000000000000000000",
        {"name": "mistudio", "auth": {"accessToken": {"role": "read"}}},
    )
    await store_token(client, "hf_readonly000000000000000000")
    pub = await publish_and_run(client, publisher, version_id, build["id"])
    assert pub["status"] == "refused"
    assert [c["evidence"]["scope"] for c in pub["error"]["details"]["checks"]] == ["read"]
    await store_token(client, "hf_revoked00000000000000000000")
    pub = await publish_and_run(client, publisher, version_id, build["id"])
    assert [c["evidence"]["scope"] for c in pub["error"]["details"]["checks"]] == ["invalid"]
    assert REPO not in publisher.hub.repos


async def test_ec7_a_second_active_publish_to_one_repository_is_refused(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    version_id, build = await _ready(client, driver, publisher, data_dir)
    first = await publish(client, publisher, version_id, build["id"])
    assert first.status_code == 201
    second = await publish(client, publisher, version_id, build["id"], prose="other")
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "repo_busy"
    assert second.json()["error"]["details"]["publish_id"] == first.json()["publish_id"]


async def test_ec8_identical_content_records_no_change_and_no_commit(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    version_id, build = await _ready(client, driver, publisher, data_dir)
    first = await publish_and_run(client, publisher, version_id, build["id"])
    commits = publisher.hub.commit_count(REPO)
    again = await publish_and_run(client, publisher, version_id, build["id"])
    assert again["status"] == "no_change", again["error"]
    assert again["commit"] == first["commit"]
    assert publisher.hub.commit_count(REPO) == commits


async def test_ec9_cancel_before_upload_cancels(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
    monkeypatch,
) -> None:
    from src.core import cancellation

    version_id, build = await _ready(client, driver, publisher, data_dir)
    monkeypatch.setattr(cancellation.CancelCheck, "poll_now", lambda self: True)
    pub = await publish_and_run(client, publisher, version_id, build["id"])
    assert pub["status"] == "cancelled"
    assert not [c for c in publisher.hub.calls if c[0] == "create_commit"]


async def test_ec9_cancel_during_upload_is_too_late_and_verification_runs(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
    monkeypatch,
) -> None:
    from src.core import cancellation

    calls = iter([False, True])
    monkeypatch.setattr(cancellation.CancelCheck, "poll_now", lambda self: next(calls))
    version_id, build = await _ready(client, driver, publisher, data_dir)
    pub = await publish_and_run(client, publisher, version_id, build["id"])
    assert pub["status"] == "published"
    assert pub["cancel_too_late"] is True


async def test_ec14_a_foreign_file_refuses_before_any_upload(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    publisher.hub.add_foreign_file(REPO, "notes.txt")
    version_id, build = await _ready(client, driver, publisher, data_dir)
    pub = await publish_and_run(client, publisher, version_id, build["id"])
    assert pub["status"] == "refused"
    assert pub["error"]["code"] == "repo_has_foreign_files"
    assert pub["error"]["details"]["paths"] == ["notes.txt"]
    assert not [c for c in publisher.hub.calls if c[0] == "create_commit"]


async def test_ec13_an_oversize_split_refuses_before_any_upload(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
    monkeypatch,
) -> None:
    from src.core.config import get_settings

    version_id, build = await _ready(client, driver, publisher, data_dir)
    monkeypatch.setattr(get_settings(), "publish_max_file_bytes", 100)
    pub = await publish_and_run(client, publisher, version_id, build["id"])
    assert pub["status"] == "refused" and pub["error"]["code"] == "split_too_large"
    assert pub["error"]["details"]["limit"] == 100
    assert pub["error"]["details"]["split"] in {f["name"] for f in build["files"]}
    assert REPO not in publisher.hub.repos


async def test_republish_adds_a_commit_and_keeps_the_history(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    from src.services.publishing.card import parse_history

    version_id, build = await _ready(client, driver, publisher, data_dir)
    first = await publish_and_run(client, publisher, version_id, build["id"])
    second_version = await built_version(client, driver, data_dir, name="humor2")
    build2 = await completed_build(client, publisher, second_version)
    second = await publish_and_run(client, publisher, second_version, build2["id"])
    assert second["status"] == "published", second["error"]
    assert second["commit"] != first["commit"]
    assert publisher.hub.commit_count(REPO) == 3  # initial + two publishes; never rewritten
    history = parse_history(publisher.hub.head_files(REPO)["README.md"].decode())
    assert [h["version"] for h in history] == [version_id, second_version]
    assert history[0]["commit"] == first["commit"], "the earlier row resolves to its real commit"


async def test_a_card_only_republish_changes_the_card_and_verifies(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    version_id, build = await _ready(client, driver, publisher, data_dir)
    first = await publish_and_run(client, publisher, version_id, build["id"])
    response = await client.post(
        f"/api/v1/publishes/{first['id']}/card", json={"card_prose": "# Humor\n\nBetter words."}
    )
    assert response.status_code == 201, response.text
    publisher.run_publish_tasks()
    pub = (await client.get(f"/api/v1/publishes/{response.json()['publish_id']}")).json()
    assert pub["status"] == "published" and pub["kind"] == "card_only"
    assert b"Better words." in publisher.hub.head_files(REPO)["README.md"]
    assert all(f["match"] for f in pub["files"])


async def test_reverify_reports_matching_hashes_and_head_drift(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    version_id, build = await _ready(client, driver, publisher, data_dir)
    pub = await publish_and_run(client, publisher, version_id, build["id"])
    publisher.hub.add_foreign_file(REPO, "later.txt")
    response = await client.post(f"/api/v1/publishes/{pub['id']}/reverify")
    assert response.status_code == 202
    publisher.run_publish_tasks()
    job = publisher.job(response.json()["job_id"])
    assert job.status == "completed"
    assert job.result is not None
    assert job.result["status"] == "hashes_match"
    assert job.result["head_moved"] is True


async def test_a_published_version_cannot_be_deleted(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    """002's delete asks 008 (``delete_guards``): the Hub copy is the record of what shipped."""
    version_id, build = await _ready(client, driver, publisher, data_dir)
    await publish_and_run(client, publisher, version_id, build["id"])
    response = await client.request(
        "DELETE", f"/api/v1/versions/{version_id}", json={"reason": "tidy up"}
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "version_published"
