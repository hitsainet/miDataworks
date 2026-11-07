"""Rule sets travel with the data, and check C-8 (FR-016.26 - FR-016.28; FTID 016 sections 3.8, 7.3).

Real PostgreSQL, the production operator registry for the build (``rule_set@1`` runs for real), the
publish worker's real task bodies against the faithful fake Hub, real exports. Both fixtures.

- every writer that ships a manifest lists the rule sets the build ran; the three that write files
  write each document's EXACT stored bytes at the listed path (sha256 equal), and a publish's Hub
  hash comparison covers them;
- a version built before rule sets were recorded reads "not recorded", never an empty list; one built
  after with no rule-set step reads recorded and empty;
- a fake consumer re-runs every carried rule set's cases through ``rule_set@1`` and accepts the
  build; one altered byte is refused on sha256;
- C-8 refuses a public publish of a version carrying a rule set with a non-public value, and allows a
  private one.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pyarrow as pa
import pytest
from sqlalchemy import text

from src.core.database import get_sync_engine, sync_session_factory
from src.core.storage import export_dir, publish_dir
from src.models import Job, PublishBuild
from src.operators.native.rules.rule_set_op import RuleSetOp
from src.rules import rule_set
from src.rules.cases import case_input
from tests.integration.rules.conftest import fixture_document, saved
from tests.integration.rules.test_rule_set_build import ROLES, case_rows, recipe, rs_step
from tests.integration.test_version_build import request, setup, src
from tests.support.fake_hub import FakeHub
from tests.support.operator_build_driver import RealDriver
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

pytestmark = pytest.mark.usefixtures("operator_name")
LISTING = "midataworks.rule-sets/v1"


@pytest.fixture
def rule_publisher(
    monkeypatch: pytest.MonkeyPatch, production_driver: RealDriver
) -> Iterator[PublishDriver]:
    """The publish worker's real tasks on the fake Hub, beside the production build driver."""
    from src.core.celery_app import celery_app
    from src.workers import publish_tasks

    hub = FakeHub()
    hub.add_token(TOKEN)
    drv = PublishDriver(hub, production_driver)  # type: ignore[arg-type]
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


async def version_with(
    client: httpx.AsyncClient,
    driver: RealDriver,
    data_dir: Path,
    steps: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    name: str,
    target: str = "detector",
) -> str:
    source = make_source(data_dir, {"train": rows}, repo_id=f"t/{name}")
    ds, rev = await setup(client, recipe(*steps), name=name, target=target)
    response = await request(client, ds, rev, [src(source)], seed=7, column_roles=ROLES)
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    assert driver.run(job_id) == "completed", driver.status(job_id)
    with sync_session_factory()() as db:
        job = db.get(Job, job_id)
        assert job is not None and job.result is not None
        return str(job.result["version_id"])


def _labelled_rows(document: dict[str, Any]) -> list[dict[str, Any]]:
    return [{**r, "label": n % 2, "prompt": r["text"]} for n, r in enumerate(case_rows(document))]


async def _two_rule_set_version(
    client: httpx.AsyncClient,
    driver: RealDriver,
    data_dir: Path,
    name: str,
    target: str = "detector",
) -> tuple[str, list[dict[str, Any]]]:
    alpha = await saved(client, fixture_document("fixture-alpha"))
    beta = await saved(client, fixture_document("fixture-beta"))
    rows = _labelled_rows(fixture_document("fixture-alpha")) + _labelled_rows(
        fixture_document("fixture-beta")
    )
    steps = [
        rs_step(alpha, "label", element_column="element", output_prefix=""),
        rs_step(beta, "screen", output_prefix=""),
    ]
    return await version_with(client, driver, data_dir, steps, rows, name, target), [alpha, beta]


def _stored_bytes() -> dict[str, bytes]:
    with get_sync_engine().connect() as conn:
        rows = conn.execute(
            text(
                "SELECT name, number, body_canonical FROM dw_config_versions WHERE kind = 'rule_set'"
            )
        ).all()
    return {f"rule_sets/{n}@{v}.json": c.encode("utf-8") for n, v, c in rows}


def _assert_listing(
    manifest: dict[str, Any], saved_sets: list[dict[str, Any]], uses: list[str]
) -> None:
    listing = manifest["extensions"][LISTING]
    assert listing["recorded"] is True
    assert [(r["name"], r["version"], r["sha256"], r["use"]) for r in listing["rule_sets"]] == [
        (s["name"], s["version"], s["sha256"], u) for s, u in zip(saved_sets, uses, strict=True)
    ]
    for r in listing["rule_sets"]:
        assert r["path"] == f"rule_sets/{r['name']}@{r['version']}.json"
    rule_set.RuleSetListing.model_validate(listing)


# --- the four writers (FR-016.26) -----------------------------------------------------------------


async def test_a_private_publish_carries_and_hash_checks_the_rule_sets(
    client: httpx.AsyncClient,
    production_driver: RealDriver,
    rule_publisher: PublishDriver,
    data_dir: Path,
) -> None:
    await store_token(client)
    version_id, sets = await _two_rule_set_version(client, production_driver, data_dir, "carry")
    build = await completed_build(client, rule_publisher, version_id)
    with sync_session_factory()() as db:
        row = db.get(PublishBuild, build["id"])
        assert row is not None and row.rule_set_files is not None
        written = {f["path"]: f for f in row.rule_set_files}
        assert row.projection["rule_sets"] == "v1"
    stored = _stored_bytes()
    assert set(written) == set(stored)
    for path, content in stored.items():
        local = publish_dir(build["id"]) / path
        assert local.read_bytes() == content
        assert written[path]["sha256"] == hashlib.sha256(content).hexdigest()
    pub = await publish_and_run(client, rule_publisher, version_id, build["id"])
    assert pub["status"] == "published", pub["error"]
    files = rule_publisher.hub.head_files(REPO)
    for path, content in stored.items():
        assert files[path] == content, path
    checked = {f["path"]: f for f in pub["files"]}
    for path in stored:
        assert checked[path]["role"] == "rule_set" and checked[path]["match"] is True
    manifest = json.loads(files["midataworks-dataset-version.json"])
    _assert_listing(manifest, sets, ["label", "screen"])
    readme = files["README.md"].decode()
    for path in stored:
        assert path in readme, "the card's digests list the rule-set files"
    c8 = next(c for c in pub["check_snapshot"] if c["check"] == "C-8")
    assert c8["outcome"] == "green", c8


async def test_a_build_without_the_carried_files_is_refused_before_any_upload(
    client: httpx.AsyncClient,
    production_driver: RealDriver,
    rule_publisher: PublishDriver,
    data_dir: Path,
) -> None:
    """Review R1 (slices 2-4), the junction of the two halves: the manifest's listing is read at
    publish time from the version, the files from the BUILD. A build named by id that predates
    016 (``rule_set_files`` NULL; a re-push reuses its parent's build too) listed files it never
    uploaded. The publish now refuses before anything reaches the Hub."""
    await store_token(client)
    version_id, _ = await _two_rule_set_version(client, production_driver, data_dir, "stale")
    build = await completed_build(client, rule_publisher, version_id)
    with get_sync_engine().begin() as conn:  # the build as one made before 0037 left it
        conn.execute(
            text("UPDATE dw_publish_builds SET rule_set_files = NULL WHERE id = :b"),
            {"b": build["id"]},
        )
    pub = await publish_and_run(client, rule_publisher, version_id, build["id"])
    assert pub["status"] == "refused", pub
    assert pub["error"]["code"] == "build_lacks_rule_sets"
    assert pub["error"]["details"]["in_build"] == []
    assert sorted(pub["error"]["details"]["listed"]) == sorted(_stored_bytes())
    hub = rule_publisher.hub
    assert REPO not in hub.repos or hub.commit_count(REPO) == 0, "nothing reached the Hub"


async def test_the_draft_and_the_exports_list_and_write_the_same_files(
    client: httpx.AsyncClient,
    production_driver: RealDriver,
    rule_publisher: PublishDriver,
    data_dir: Path,
) -> None:
    version_id, sets = await _two_rule_set_version(
        client, production_driver, data_dir, "writers", target="untyped"
    )
    build = await completed_build(client, rule_publisher, version_id, label_column=None)
    draft = await client.get(
        f"/api/v1/versions/{version_id}/card-draft", params={"build_id": build["id"]}
    )
    assert draft.status_code == 200, draft.text
    handoff = await client.get(f"/api/v1/versions/{version_id}/handoff-manifest")
    assert handoff.status_code == 200, handoff.text
    _assert_listing(handoff.json(), sets, ["label", "screen"])  # the draft document's listing
    stored = _stored_bytes()
    export = await client.post(
        "/api/v1/exports",
        json={"target": "miforge_set", "version_id": version_id, "miforge_set_kind": "corpus"},
    )
    assert export.status_code == 202, export.text
    rule_publisher.run_publish_tasks()
    row = (await client.get(f"/api/v1/exports/{export.json()['export_id']}")).json()
    assert row["status"] == "completed", row
    out = export_dir(row["id"])
    manifest = json.loads((out / "midataworks-dataset-version.json").read_text())
    _assert_listing(manifest, sets, ["label", "screen"])
    for path, content in stored.items():
        assert (out / path).read_bytes() == content
        listed = next(f for f in row["files"] if f["path"] == path)
        assert listed["role"] == "rule_set"
        assert listed["sha256"] == hashlib.sha256(content).hexdigest()
    served = await client.get(f"/api/v1/exports/{row['id']}/files/{next(iter(stored))}")
    assert served.status_code == 200 and served.content == stored[next(iter(stored))]


async def test_the_reward_bundle_lists_and_writes_the_rule_sets(
    client: httpx.AsyncClient,
    production_driver: RealDriver,
    rule_publisher: PublishDriver,
    data_dir: Path,
) -> None:
    version_id, sets = await _two_rule_set_version(
        client, production_driver, data_dir, "bundle", target="grpo_prompt"
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
    rule_publisher.run_publish_tasks()
    row = (await client.get(f"/api/v1/exports/{response.json()['export_id']}")).json()
    assert row["status"] == "completed", row
    out = export_dir(row["id"])
    manifest = json.loads((out / "midataworks-dataset-version.json").read_text())
    _assert_listing(manifest, sets, ["label", "screen"])
    for path, content in _stored_bytes().items():
        assert (out / path).read_bytes() == content


# --- recorded / not recorded (FR-016.26) and the projection key -----------------------------------


async def test_a_version_built_before_016_reads_not_recorded(
    client: httpx.AsyncClient, production_driver: RealDriver, data_dir: Path
) -> None:
    rows = case_rows(fixture_document("fixture-beta"))
    steps = [{"operator": "drop_columns", "version": "1", "params": {"columns": ["element"]}}]
    after = await version_with(client, production_driver, data_dir, steps, rows, "after")
    body = (await client.get(f"/api/v1/versions/{after}")).json()
    assert body["rule_sets"] == {"recorded": True, "rule_sets": []}
    # a version as 0037 found it: built before rule sets were recorded (the flag was false)
    with get_sync_engine().begin() as conn:
        conn.execute(text("ALTER TABLE dw_versions DISABLE TRIGGER dw_versions_immutable"))
        conn.execute(
            text("UPDATE dw_versions SET rule_sets_recorded = false WHERE id = CAST(:v AS uuid)"),
            {"v": after},
        )
        conn.execute(text("ALTER TABLE dw_versions ENABLE TRIGGER dw_versions_immutable"))
    before = (await client.get(f"/api/v1/versions/{after}")).json()
    assert before["rule_sets"] == {"recorded": False, "rule_sets": None}


async def test_a_build_made_before_the_rule_set_key_is_not_reused(
    client: httpx.AsyncClient,
    production_driver: RealDriver,
    rule_publisher: PublishDriver,
    data_dir: Path,
) -> None:
    from src.services.publishing import projection

    version_id, _ = await _two_rule_set_version(client, production_driver, data_dir, "reuse")
    from src.services.publishing import feature_seams

    current = projection.projection_spec(
        version_id, "label", review_layer=feature_seams.effective_labels_available()
    )
    old_spec = {k: v for k, v in current.items() if k != "rule_sets"}
    with sync_session_factory()() as db:
        db.add(
            PublishBuild(
                id="pbld_old016",
                version_id=version_id,
                projection_digest=projection.projection_digest(old_spec),
                projection=old_spec,
                status="completed",
                files=[],
                started_by="Test Operator",
                started_by_origin="operator",
            )
        )
        db.commit()
    build = await completed_build(client, rule_publisher, version_id)
    assert build["id"] != "pbld_old016"


# --- the fake consumer (FR-016.27) -----------------------------------------------------------------


def consume(manifest: dict[str, Any], files: dict[str, bytes]) -> list[str]:
    """What a consumer does: each listed file must hash to its listing, parse as a rule set, and
    pass its own cases through ``rule_set@1``'s real run. Returns the names accepted."""
    accepted = []
    for entry in manifest["extensions"][LISTING]["rule_sets"]:
        content = files[entry["path"]]
        if hashlib.sha256(content).hexdigest() != entry["sha256"]:
            raise ValueError(f"{entry['path']}: sha256 differs from the listing")
        document = json.loads(content)
        texts, elements = zip(*(case_input(c) for c in document["cases"]), strict=True)
        batch = pa.table(
            {
                "_dw_row_key": [f"k{i}" for i in range(len(texts))],
                "_dw_occurrence": [0] * len(texts),
                "text": list(texts),
                "element": pa.array(list(elements), pa.string()),
            }
        )
        params = {
            "rule_set_name": entry["name"],
            "rule_set_version": entry["version"],
            "rule_set_sha256": entry["sha256"],
            "use": entry["use"],
            "text_column": "text",
            "element_column": "element",
            "output_prefix": "",
            "rule_set_document": content.decode("utf-8"),
        }
        rows = RuleSetOp().run(batch, params, None).output.to_pylist()  # type: ignore[arg-type]
        for row, case in zip(rows, document["cases"], strict=True):
            for column, value in case["expect"]["outputs"].items():
                if row[column] != value:
                    raise ValueError(f"{entry['name']}: case {case['id']} fails")
        accepted.append(entry["name"])
    return accepted


async def test_a_fake_consumer_reruns_the_carried_cases_and_refuses_an_altered_byte(
    client: httpx.AsyncClient,
    production_driver: RealDriver,
    rule_publisher: PublishDriver,
    data_dir: Path,
) -> None:
    await store_token(client)
    version_id, sets = await _two_rule_set_version(client, production_driver, data_dir, "consumer")
    build = await completed_build(client, rule_publisher, version_id)
    pub = await publish_and_run(client, rule_publisher, version_id, build["id"])
    assert pub["status"] == "published", pub["error"]
    files = rule_publisher.hub.head_files(REPO)
    manifest = json.loads(files["midataworks-dataset-version.json"])
    assert sorted(consume(manifest, files)) == sorted(s["name"] for s in sets)
    altered = dict(files)
    path = manifest["extensions"][LISTING]["rule_sets"][0]["path"]
    content = bytearray(altered[path])
    content[-2] = ord(" ") if content[-2] != ord(" ") else ord("\t")
    altered[path] = bytes(content)
    with pytest.raises(ValueError, match="sha256 differs"):
        consume(manifest, altered)


# --- C-8 (FR-016.28) ----------------------------------------------------------------------------


async def test_c8_refuses_a_public_publish_carrying_a_non_public_value_and_allows_private(
    client: httpx.AsyncClient,
    production_driver: RealDriver,
    rule_publisher: PublishDriver,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owners_report_green(monkeypatch)
    await store_token(client)
    document = fixture_document("fixture-alpha")
    leaky = copy.deepcopy(document)
    leaky["name"] = "alpha-leaky"
    # a constructed case whose value reads valid as a card number: not a public value (D28)
    leaky["cases"][0]["source"] = {"kind": "constructed"}
    out = await saved(client, leaky)
    rows = _labelled_rows(document)
    version_id = await version_with(
        client,
        production_driver,
        data_dir,
        [rs_step(out, "label", element_column="element", output_prefix="")],
        rows,
        "leaky",
    )
    build = await completed_build(client, rule_publisher, version_id)
    public = await publish_and_run(
        client, rule_publisher, version_id, build["id"], visibility="public"
    )
    assert public["status"] == "refused", public
    blocking = public["error"]["details"]["checks"]
    assert [c["check"] for c in blocking] == ["C-8"], blocking
    assert "alpha-leaky@1" in blocking[0]["reason"]
    assert document["cases"][0]["id"] in json.dumps(blocking[0]["evidence"])
    private = await publish_and_run(
        client, rule_publisher, version_id, build["id"], repo="mistudio/leaky-private"
    )
    assert private["status"] == "published", private["error"]
