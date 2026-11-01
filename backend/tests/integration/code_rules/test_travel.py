"""Code rules travel with the data, and stay honest (feature 018 slice 4; FTASKS 14.1 – 14.5;
FR-018.40 – FR-018.42).

Real PostgreSQL, the production registry and executor for the build (``rule_set@1`` asks the
recording fake runner behind the REAL client), the publish worker's real task bodies against the
faithful fake Hub, real exports. Both fixture behaviours where the behaviour matters.

- a publish build writes TWO files per carried code rule: ``code_rules/<sha>.json`` (exactly the
  hashed bytes) and ``code_rules/<sha>.cases.json``; the publish uploads and hash-compares them
  (role ``code_rule``) and the manifest lists them under ``midataworks.code-rules/v1``;
- every manifest writer lists the same code rules (publish, draft card, export, reward bundle) and
  the three that write files write the same bytes;
- a fake consumer re-hashes the document file to the listed ``code_sha256`` and re-runs its cases
  through the runner; one altered byte is refused on the hash (M24);
- C-9 refuses a public push of a version carrying a code rule whose floor is ``private_only``; C-8
  refuses one whose cases carry a non-public value; private pushes are allowed;
- after a revocation the version reads "built with a since-revoked code rule", its files and hashes
  unchanged; a version built before 018 reads "not recorded".
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from src.core.canonical_json import canonical_json
from src.core.database import get_sync_engine, sync_session_factory
from src.core.storage import export_dir, publish_dir
from src.models import Job, PublishBuild
from src.runner_contract.document import CodeRuleListing
from src.runner_contract.hashing import code_sha256
from tests.integration.code_rules.conftest import CR, approved_rule, revoke, save, saved_rule_set
from tests.integration.code_rules.test_build_dispatch import recipe, rs_step, texts_for
from tests.integration.test_version_build import request, setup, src
from tests.support import code_rule_fixtures as fx
from tests.support.fake_hub import FakeHub
from tests.support.fake_runner import FakeRunner
from tests.support.publish_fixtures import (
    REPO,
    TOKEN,
    PublishDriver,
    completed_build,
    owners_report_green,
    publish_and_run,
    store_token,
)
from tests.support.version_fixtures import make_source

pytestmark = pytest.mark.usefixtures("operator_name", "production_driver")
LISTING = "midataworks.code-rules/v1"
ROLES = {"text": "content"}


@pytest.fixture
def code_publisher(monkeypatch: pytest.MonkeyPatch, production_driver: Any) -> Iterator[Any]:
    """The publish worker's real tasks on the fake Hub, beside the production build driver."""
    from src.core.celery_app import celery_app
    from src.workers import publish_tasks

    hub = FakeHub()
    hub.add_token(TOKEN)
    drv = PublishDriver(hub, production_driver)
    builds = celery_app.send_task

    def send(name: str, args: list[Any] | None = None, **kwargs: Any) -> Any:
        if name.startswith("midataworks.publish."):
            return drv.send_task(name, args, **kwargs)
        return builds(name, args, **kwargs)

    monkeypatch.setattr(celery_app, "send_task", send)
    monkeypatch.setattr(publish_tasks, "emit", drv.emit)
    monkeypatch.setattr(publish_tasks, "HUB_API_FACTORY", hub.api)
    monkeypatch.setattr(publish_tasks, "_next_jobs", lambda: None)
    yield drv


async def built_with(
    client: httpx.AsyncClient,
    driver: Any,
    data_dir: Path,
    behaviour: str,
    name: str,
    *,
    target: str = "detector",
    save_body: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """A version whose build ran the behaviour's code rule (use label): (version id, rule, set)."""
    if save_body is None:
        rule = await approved_rule(client, behaviour)
    else:
        response = await save(client, save_body)
        assert response.status_code == 201, response.text
        rule = response.json()
    rs = await saved_rule_set(client, fx.rule_set_document(behaviour, rule["code_sha256"]))
    rows = [{"text": t, "label": n % 2, "prompt": t} for n, t in enumerate(texts_for(behaviour))]
    source = make_source(data_dir, {"train": rows}, repo_id=f"t/{name}")
    ds, rev = await setup(client, recipe(rs_step(rs, use="label")), name=name, target=target)
    response = await request(client, ds, rev, [src(source)], seed=7, column_roles=ROLES)
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    assert driver.run(job_id) == "completed", driver.status(job_id)
    with sync_session_factory()() as db:
        job = db.get(Job, job_id)
        assert job is not None and job.result is not None
        return str(job.result["version_id"]), rule, rs


def stored(sha: str) -> tuple[bytes, bytes]:
    """The exact hashed document bytes, and the cases file's canonical bytes."""
    with get_sync_engine().connect() as conn:
        canonical, body = conn.execute(
            text(
                "SELECT body_canonical, body FROM dw_config_versions WHERE kind = 'code_rule' "
                "AND body_sha256 = :s ORDER BY number DESC LIMIT 1"
            ),
            {"s": sha},
        ).one()
    return canonical.encode("utf-8"), canonical_json(body["cases"])


def assert_listing(manifest: dict[str, Any], rule: dict[str, Any], rs: dict[str, Any]) -> None:
    listing = manifest["extensions"][LISTING]
    CodeRuleListing.model_validate(listing)
    (entry,) = listing["code_rules"]
    sha = rule["code_sha256"]
    document, cases = stored(sha)
    assert entry["code_sha256"] == sha == hashlib.sha256(document).hexdigest()
    assert entry["path"] == f"code_rules/{sha}.json"
    assert entry["cases_path"] == f"code_rules/{sha}.cases.json"
    assert entry["cases_sha256"] == hashlib.sha256(cases).hexdigest()
    assert entry["rule_sets"] == [f"{rs['name']}@1"]
    assert entry["runner_contract"] == "midataworks.runner/v1"
    assert entry["visibility_floor"] == "none"


# --- 14.1: publish builds write and hash-check both files --------------------------------------


async def test_a_private_publish_carries_and_hash_checks_the_code_rule_files(
    client: httpx.AsyncClient,
    fake: FakeRunner,
    production_driver: Any,
    code_publisher: Any,
    data_dir: Path,
    behaviour: str,
) -> None:
    await store_token(client)
    version_id, rule, rs = await built_with(
        client, production_driver, data_dir, behaviour, f"{behaviour}-carry"
    )
    build = await completed_build(client, code_publisher, version_id)
    sha = rule["code_sha256"]
    document, cases = stored(sha)
    expected = {f"code_rules/{sha}.json": document, f"code_rules/{sha}.cases.json": cases}
    with sync_session_factory()() as db:
        row = db.get(PublishBuild, build["id"])
        assert row is not None and row.code_rule_files is not None
        written = {f["path"]: f for f in row.code_rule_files}
        assert row.projection["code_rules"] == "v1"
    assert set(written) == set(expected)
    for path, content in expected.items():
        assert (publish_dir(build["id"]) / path).read_bytes() == content
        assert written[path]["sha256"] == hashlib.sha256(content).hexdigest()
    pub = await publish_and_run(client, code_publisher, version_id, build["id"])
    assert pub["status"] == "published", pub["error"]
    files = code_publisher.hub.head_files(REPO)
    checked = {f["path"]: f for f in pub["files"]}
    for path, content in expected.items():
        assert files[path] == content, path
        assert checked[path]["role"] == "code_rule" and checked[path]["match"] is True
    manifest = json.loads(files["midataworks-dataset-version.json"])
    assert_listing(manifest, rule, rs)
    readme = files["README.md"].decode()
    assert all(p in readme for p in expected), "the card's digests list the code-rule files"
    for check in ("C-8", "C-9"):
        found = next(c for c in pub["check_snapshot"] if c["check"] == check)
        assert found["outcome"] == "green", found


async def test_a_build_without_the_code_rule_files_is_refused_before_any_upload(
    client: httpx.AsyncClient,
    fake: FakeRunner,
    production_driver: Any,
    code_publisher: Any,
    data_dir: Path,
) -> None:
    await store_token(client)
    version_id, rule, _ = await built_with(
        client, production_driver, data_dir, "fixture-gamma", "stale-code"
    )
    build = await completed_build(client, code_publisher, version_id)
    with get_sync_engine().begin() as conn:  # a build as one made before 0044 left it
        conn.execute(
            text("UPDATE dw_publish_builds SET code_rule_files = NULL WHERE id = :b"),
            {"b": build["id"]},
        )
    pub = await publish_and_run(client, code_publisher, version_id, build["id"])
    assert pub["status"] == "refused", pub
    assert pub["error"]["code"] == "build_lacks_code_rules"
    assert pub["error"]["details"]["in_build"] == []
    hub = code_publisher.hub
    assert REPO not in hub.repos or hub.commit_count(REPO) == 0, "nothing reached the Hub"


# --- 14.2: every writer lists the same code rules ----------------------------------------------


async def test_the_draft_and_the_exports_list_and_write_the_same_files(
    client: httpx.AsyncClient,
    fake: FakeRunner,
    production_driver: Any,
    code_publisher: Any,
    data_dir: Path,
) -> None:
    version_id, rule, rs = await built_with(
        client, production_driver, data_dir, "fixture-gamma", "writers", target="untyped"
    )
    build = await completed_build(client, code_publisher, version_id, label_column=None)
    handoff = await client.get(f"/api/v1/versions/{version_id}/handoff-manifest")
    assert handoff.status_code == 200, handoff.text
    assert_listing(handoff.json(), rule, rs)  # the draft document's listing
    draft = await client.get(
        f"/api/v1/versions/{version_id}/card-draft", params={"build_id": build["id"]}
    )
    assert draft.status_code == 200, draft.text
    sha = rule["code_sha256"]
    document, cases = stored(sha)
    expected = {f"code_rules/{sha}.json": document, f"code_rules/{sha}.cases.json": cases}
    export = await client.post(
        "/api/v1/exports",
        json={"target": "miforge_set", "version_id": version_id, "miforge_set_kind": "corpus"},
    )
    assert export.status_code == 202, export.text
    code_publisher.run_publish_tasks()
    row = (await client.get(f"/api/v1/exports/{export.json()['export_id']}")).json()
    assert row["status"] == "completed", row
    out = export_dir(row["id"])
    assert_listing(json.loads((out / "midataworks-dataset-version.json").read_text()), rule, rs)
    for path, content in expected.items():
        assert (out / path).read_bytes() == content
        listed = next(f for f in row["files"] if f["path"] == path)
        assert listed["role"] == "code_rule"
        assert listed["sha256"] == hashlib.sha256(content).hexdigest()


async def test_the_reward_bundle_lists_and_writes_the_code_rules(
    client: httpx.AsyncClient,
    fake: FakeRunner,
    production_driver: Any,
    code_publisher: Any,
    data_dir: Path,
) -> None:
    version_id, rule, rs = await built_with(
        client, production_driver, data_dir, "fixture-delta", "bundle", target="grpo_prompt"
    )
    rubric = await client.post(
        "/api/v1/rubrics",
        json={
            "name": "carry/judge",
            "body": {
                "style": "binary",
                "messages": [
                    {
                        "role": "user",
                        "content": "Prompt: {prompt}\nAnswer: {completion}\nVERDICT: yes or no.",
                    }
                ],
                "input_fields": ["prompt", "completion"],
                "parser": "verdict_line_v1",
                "allowed_verdicts": ["yes", "no"],
            },
        },
    )
    assert rubric.status_code == 201, rubric.text
    response = await client.post(
        "/api/v1/exports",
        json={
            "target": "reward_bundle",
            "version_id": version_id,
            "rubric_id": rubric.json()["id"],
            "judge_model_id": "judge-7b",
            "verdict_scores": {"yes": 1.0, "no": 0.0},
            "seed": 3,
        },
    )
    assert response.status_code == 202, response.text
    code_publisher.run_publish_tasks()
    row = (await client.get(f"/api/v1/exports/{response.json()['export_id']}")).json()
    assert row["status"] == "completed", row
    out = export_dir(row["id"])
    assert_listing(json.loads((out / "midataworks-dataset-version.json").read_text()), rule, rs)
    document, cases = stored(rule["code_sha256"])
    assert (out / f"code_rules/{rule['code_sha256']}.json").read_bytes() == document
    assert (out / f"code_rules/{rule['code_sha256']}.cases.json").read_bytes() == cases


async def test_a_version_built_before_018_reads_not_recorded(
    client: httpx.AsyncClient, fake: FakeRunner, production_driver: Any, data_dir: Path
) -> None:
    version_id, rule, _ = await built_with(
        client, production_driver, data_dir, "fixture-gamma", "recorded"
    )
    body = (await client.get(f"/api/v1/versions/{version_id}")).json()
    assert body["code_rules"]["recorded"] is True
    assert [c["code_sha256"] for c in body["code_rules"]["code_rules"]] == [rule["code_sha256"]]
    with get_sync_engine().begin() as conn:  # a version as 0044 found it, with no use row
        conn.execute(text("ALTER TABLE dw_versions DISABLE TRIGGER dw_versions_immutable"))
        conn.execute(text("SET LOCAL dw.purge = 'on'"))
        conn.execute(text("DELETE FROM dw_code_rule_uses"))
        conn.execute(
            text("UPDATE dw_versions SET code_rules_recorded = false WHERE id = CAST(:v AS uuid)"),
            {"v": version_id},
        )
        conn.execute(text("ALTER TABLE dw_versions ENABLE TRIGGER dw_versions_immutable"))
    before = (await client.get(f"/api/v1/versions/{version_id}")).json()
    assert before["code_rules"] == {"recorded": False, "code_rules": None}


# --- 14.3: the fake consumer -------------------------------------------------------------------


def consume(manifest: dict[str, Any], files: dict[str, bytes], fake: FakeRunner) -> list[str]:
    """What a consumer does (miStudio 040, miLLM 039): each listed document must hash to its
    listing by the contract's hash rule, and pass its own cases through the runner."""
    from src.clients.runner_client import DocumentRef
    from src.runner_contract.document import CodeRuleCase
    from src.services.code_rules import cases as case_runs
    from src.services.code_rules import limits

    accepted = []
    for entry in manifest["extensions"][LISTING]["code_rules"]:
        content = files[entry["path"]]
        if code_sha256(json.loads(content)) != entry["code_sha256"]:
            raise ValueError(f"{entry['path']}: the contract hash differs from the listing")
        cases = [CodeRuleCase.model_validate(c) for c in json.loads(files[entry["cases_path"]])]
        answer = limits.sync_client().run(
            DocumentRef(entry["code_sha256"], content.decode("utf-8")),
            case_runs.calls_for(cases, 1),
            mode="save_check",
            lane="interactive",
            limits=limits.floor(),
            send_document=True,
        )
        if not all(v.passed for v in case_runs.verdicts(cases, answer.results)):
            raise ValueError(f"{entry['name']}: a case fails")
        accepted.append(entry["name"])
    return accepted


async def test_a_fake_consumer_rehashes_and_reruns_and_refuses_an_altered_byte_m24(
    client: httpx.AsyncClient,
    fake: FakeRunner,
    production_driver: Any,
    code_publisher: Any,
    data_dir: Path,
    behaviour: str,
) -> None:
    await store_token(client)
    version_id, rule, _ = await built_with(
        client, production_driver, data_dir, behaviour, f"{behaviour}-consumer"
    )
    build = await completed_build(client, code_publisher, version_id)
    pub = await publish_and_run(client, code_publisher, version_id, build["id"])
    assert pub["status"] == "published", pub["error"]
    files = code_publisher.hub.head_files(REPO)
    manifest = json.loads(files["midataworks-dataset-version.json"])
    assert consume(manifest, files, fake) == [fx.fixture(behaviour)["name"]]
    path = f"code_rules/{rule['code_sha256']}.json"
    altered = dict(files)
    document = json.loads(altered[path])
    document["source"] = document["source"] + " "
    altered[path] = canonical_json(document)
    with pytest.raises(ValueError, match="hash differs"):
        consume(manifest, altered, fake)


# --- 14.4: visibility (C-9) and public values (C-8) ----------------------------------------------


async def _private_only_version(client: httpx.AsyncClient, driver: Any, data_dir: Path) -> str:
    """Any version, then marked as one built under a declared ``private_only`` floor (017)."""
    version_id, _, _ = await built_with(client, driver, data_dir, "fixture-delta", "floor-src")
    with get_sync_engine().begin() as conn:
        conn.execute(text("ALTER TABLE dw_versions DISABLE TRIGGER dw_versions_immutable"))
        conn.execute(
            text(
                "UPDATE dw_versions SET visibility_floor = 'private_only' "
                "WHERE id = CAST(:v AS uuid)"
            ),
            {"v": version_id},
        )
        conn.execute(text("ALTER TABLE dw_versions ENABLE TRIGGER dw_versions_immutable"))
    return version_id


async def test_c9_refuses_a_public_push_carrying_a_private_only_code_rule_and_allows_private(
    client: httpx.AsyncClient,
    fake: FakeRunner,
    production_driver: Any,
    code_publisher: Any,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owners_report_green(monkeypatch)
    await store_token(client)
    private_source = await _private_only_version(client, production_driver, data_dir)
    body = fx.save_body("fixture-gamma")
    body["document"]["derived_from"] = {
        "sources": [private_source],
        "statement": "Derived from a private-only version.",
    }
    fake.twins[code_sha256(body["document"])] = fx.fixture("fixture-gamma")["twin"]
    version_id, rule, _ = await built_with(
        client, production_driver, data_dir, "fixture-gamma", "floored", save_body=body
    )
    build = await completed_build(client, code_publisher, version_id)
    public = await publish_and_run(
        client, code_publisher, version_id, build["id"], visibility="public"
    )
    assert public["status"] == "refused", public
    blocking = public["error"]["details"]["checks"]
    assert [c["check"] for c in blocking] == ["C-9"], blocking
    assert f"{fx.fixture('fixture-gamma')['name']}@1" in blocking[0]["reason"]
    evidence = blocking[0]["evidence"]
    assert evidence["code"] == "code_rule_floor_public_refused"
    assert evidence["code_rules"][0]["basis"] == [
        {"reference": private_source, "floor": "private_only"}
    ]
    assert evidence["code_rules"][0]["code_sha256"] == rule["code_sha256"]
    private = await publish_and_run(
        client, code_publisher, version_id, build["id"], repo="mistudio/floored-private"
    )
    assert private["status"] == "published", private["error"]
    manifest = json.loads(
        code_publisher.hub.head_files("mistudio/floored-private")[
            "midataworks-dataset-version.json"
        ]
    )
    assert manifest["extensions"][LISTING]["code_rules"][0]["visibility_floor"] == "private_only"


async def test_c8_refuses_a_public_push_carrying_a_code_rule_case_with_a_non_public_value(
    client: httpx.AsyncClient,
    fake: FakeRunner,
    production_driver: Any,
    code_publisher: Any,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owners_report_green(monkeypatch)
    await store_token(client)
    body = fx.save_body("fixture-gamma")
    # a constructed case whose text reads valid as a card number: not a public value (D28)
    body["cases"].append(
        {
            "id": "card-shaped",
            "function": "digit_sum",
            "params": {"sums": [10]},
            "text": "4111111111111111",
            "expect": {"count": 0},
            "source": {"kind": "constructed"},
        }
    )
    version_id, _, _ = await built_with(
        client, production_driver, data_dir, "fixture-gamma", "leaky-code", save_body=body
    )
    build = await completed_build(client, code_publisher, version_id)
    public = await publish_and_run(
        client, code_publisher, version_id, build["id"], visibility="public"
    )
    assert public["status"] == "refused", public
    blocking = public["error"]["details"]["checks"]
    assert [c["check"] for c in blocking] == ["C-8"], blocking
    assert f"{fx.fixture('fixture-gamma')['name']}@" in blocking[0]["reason"]
    private = await publish_and_run(
        client, code_publisher, version_id, build["id"], repo="mistudio/leaky-code-private"
    )
    assert private["status"] == "published", private["error"]


async def test_c8_reads_the_code_rule_document_too_not_only_its_cases(
    client: httpx.AsyncClient,
    fake: FakeRunner,
    production_driver: Any,
    code_publisher: Any,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The document travels as a public file (``code_rules/<sha>.json``), so a non-public value
    written into its source refuses a public push like one in a case (018 Review R3; the builder's
    open question 5 - if the operator settles C-8 on cases only, this test goes with it)."""
    owners_report_green(monkeypatch)
    await store_token(client)
    body = fx.save_body("fixture-gamma")
    body["document"]["source"] += "# seen on 4111111111111111\n"
    fake.twins[code_sha256(body["document"])] = fx.fixture("fixture-gamma")["twin"]
    version_id, _, _ = await built_with(
        client, production_driver, data_dir, "fixture-gamma", "leaky-source", save_body=body
    )
    build = await completed_build(client, code_publisher, version_id)
    public = await publish_and_run(
        client, code_publisher, version_id, build["id"], visibility="public"
    )
    assert public["status"] == "refused", public
    blocking = public["error"]["details"]["checks"]
    assert [c["check"] for c in blocking] == ["C-8"], blocking
    assert f"{fx.fixture('fixture-gamma')['name']}@" in blocking[0]["reason"]


# --- 14.5: revoked rules stay honest -----------------------------------------------------------


async def test_a_revocation_changes_the_state_shown_and_never_the_files(
    client: httpx.AsyncClient,
    fake: FakeRunner,
    production_driver: Any,
    code_publisher: Any,
    data_dir: Path,
    behaviour: str,
) -> None:
    version_id, rule, _ = await built_with(
        client, production_driver, data_dir, behaviour, f"{behaviour}-honest"
    )
    first = await completed_build(client, code_publisher, version_id)
    before = (await client.get(f"/api/v1/versions/{version_id}")).json()["code_rules"]
    assert before["code_rules"][0]["approval_state"] == "approved"
    await revoke(client, rule["code_sha256"], "found a fault")
    after = (await client.get(f"/api/v1/versions/{version_id}")).json()["code_rules"]
    (entry,) = after["code_rules"]
    assert entry["approval_state"] == "revoked"
    assert "since-revoked" in entry["note"]
    assert {k: v for k, v in entry.items() if k not in ("approval_state", "note")} == {
        k: v for k, v in before["code_rules"][0].items() if k not in ("approval_state", "note")
    }
    with sync_session_factory()() as db:
        row = db.get(PublishBuild, first["id"])
        assert row is not None
        files = sorted((f["path"], f["sha256"]) for f in row.code_rule_files or [])
    document, cases = stored(rule["code_sha256"])
    assert files == sorted(
        [
            (f"code_rules/{rule['code_sha256']}.json", hashlib.sha256(document).hexdigest()),
            (f"code_rules/{rule['code_sha256']}.cases.json", hashlib.sha256(cases).hexdigest()),
        ]
    )
    got = (await client.get(f"{CR}/by-hash/{rule['code_sha256']}")).json()
    assert got["canonical_utf8"].encode("utf-8") == document
