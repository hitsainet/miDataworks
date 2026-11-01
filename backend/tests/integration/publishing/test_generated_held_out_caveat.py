"""The ``generated_held_out`` caveat and the proof on each published split (017 FTASKS 2.10;
FR-017.5, FR-017.2).

Real builds (the production registry plus ``fx_generator_echo``), the publish worker's real task
bodies against the fake Hub. The passing fixture's manifest validates against the REGENERATED
published schema and carries the caveat (severity ``note``, split, columns, values, counts) in BOTH
places a dataset-version manifest is assembled: the draft (``drafts.built_document``, read through
the handoff manifest) and the publish (``publish_job.run_publish``). A version without generated
held-out rows carries none.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import jsonschema
import pytest

from src.services.curation.held_out_proof import CITATION
from tests.integration.test_directed_held_out import ECHO, KEEP, SPLIT, carriers, driver, run
from tests.integration.test_version_build import src
from tests.support.fake_hub import FakeHub
from tests.support.operator_build_driver import RealDriver
from tests.support.publish_fixtures import (
    REPO,
    TOKEN,
    PublishDriver,
    completed_build,
    publish_and_run,
    store_token,
)
from tests.support.version_fixtures import make_source

__all__ = ["driver"]
SCHEMA = json.loads(
    (
        Path(__file__).resolve().parents[4] / "docs/schemas/midataworks-dataset-version-v1.json"
    ).read_text(encoding="utf-8")
)
DIRECTED = ("ood_genre", "ood_generator", "ood_both")


@pytest.fixture
def publisher(monkeypatch: pytest.MonkeyPatch, driver: RealDriver) -> Iterator[PublishDriver]:
    from src.core.celery_app import celery_app
    from src.workers import publish_tasks

    hub = FakeHub()
    hub.add_token(TOKEN)
    drv = PublishDriver(hub, driver)  # type: ignore[arg-type]
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


async def built(
    client: httpx.AsyncClient,
    driver: RealDriver,
    data_dir: Path,
    steps: list[dict[str, Any]],
    name: str,
) -> dict[str, Any]:
    source = make_source(data_dir, {"train": carriers(name)}, repo_id=f"t/{name}")
    out = await run(client, driver, steps, [src(source)], name, target="untyped")
    assert out["status"] == "completed", out
    version: dict[str, Any] = (await client.get(f"/api/v1/versions/{out['version_id']}")).json()
    return version


def caveat(manifest: dict[str, Any]) -> dict[str, Any] | None:
    jsonschema.Draft202012Validator(SCHEMA).validate(manifest)
    found = [c for c in manifest["caveats"] if c["code"] == "generated_held_out"]
    assert len(found) <= 1
    return found[0] if found else None


def assert_caveat_and_proofs(manifest: dict[str, Any], version: dict[str, Any]) -> None:
    note = caveat(manifest)
    assert note is not None and note["severity"] == "note"
    by = {s["name"]: s for s in version["splits"]}
    detail = {d["split"]: d for d in note["detail"]["splits"]}
    assert set(detail) == set(DIRECTED) and note["detail"]["rule"] == CITATION
    for name in DIRECTED:
        proof = by[name]["proof"]
        assert detail[name]["columns"] == proof["columns"]
        assert detail[name]["held_out_values"] == proof["held_out_values"]
        assert detail[name]["generated_rows"] == proof["generated_rows"] > 0
        assert detail[name]["rows"] == proof["rows"]
        assert detail[name]["training_values"] == proof["training_values"]
    published = {s["name"]: s for s in manifest["content"]["splits"]}
    for name in DIRECTED:
        assert published[name]["extensions"]["held_out_proof"] == by[name]["proof"]
    for name in ("train", "test"):
        assert published[name]["extensions"] == {}


async def test_the_draft_and_the_publish_carry_the_caveat_and_the_proofs(
    client: httpx.AsyncClient,
    driver: RealDriver,
    publisher: PublishDriver,
    data_dir: Path,
    operator_name: str,
) -> None:
    await store_token(client)
    version = await built(client, driver, data_dir, [ECHO, SPLIT], "caveat")
    build = await completed_build(client, publisher, version["id"], label_column=None)
    draft = await client.get(f"/api/v1/versions/{version['id']}/handoff-manifest")
    assert draft.status_code == 200, draft.text
    assert_caveat_and_proofs(draft.json(), version)
    pub = await publish_and_run(client, publisher, version["id"], build["id"])
    assert pub["status"] == "published", pub["error"]
    manifest = json.loads(publisher.hub.head_files(REPO)["midataworks-dataset-version.json"])
    assert_caveat_and_proofs(manifest, version)


async def test_a_version_without_generated_held_out_rows_carries_none(
    client: httpx.AsyncClient,
    driver: RealDriver,
    publisher: PublishDriver,
    data_dir: Path,
    operator_name: str,
) -> None:
    # directed, but no generated rows: the proof is published, the caveat is not
    version = await built(client, driver, data_dir, [SPLIT], "no-generated")
    assert all(s["proof"]["generated_rows"] == 0 for s in version["splits"] if s.get("proof"))
    await completed_build(client, publisher, version["id"], label_column=None)
    draft = (await client.get(f"/api/v1/versions/{version['id']}/handoff-manifest")).json()
    assert caveat(draft) is None
    published = {s["name"]: s for s in draft["content"]["splits"]}
    assert published["ood_genre"]["extensions"]["held_out_proof"]["passes"]
    # no split at all: neither
    plain = await built(client, driver, data_dir, [KEEP], "plain")
    await completed_build(client, publisher, plain["id"], label_column=None)
    draft = (await client.get(f"/api/v1/versions/{plain['id']}/handoff-manifest")).json()
    assert caveat(draft) is None
    assert all(s["extensions"] == {} for s in draft["content"]["splits"])


async def test_the_proofs_recompute_from_the_published_files(
    client: httpx.AsyncClient,
    driver: RealDriver,
    publisher: PublishDriver,
    data_dir: Path,
    operator_name: str,
    tmp_path: Path,
) -> None:
    """FR-017.2: "recomputing from the published split files gives the same proof" - the files
    the Hub holds, read back by ``scripts/check_held_out_proof.py``."""
    from scripts import check_held_out_proof

    await store_token(client)
    version = await built(client, driver, data_dir, [ECHO, SPLIT], "recheck")
    build = await completed_build(client, publisher, version["id"], label_column=None)
    pub = await publish_and_run(client, publisher, version["id"], build["id"])
    assert pub["status"] == "published", pub["error"]
    for path, content in publisher.hub.head_files(REPO).items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_bytes(content)
    manifest_path = tmp_path / "midataworks-dataset-version.json"
    assert check_held_out_proof.main([str(manifest_path), str(tmp_path)]) == 0
    result = check_held_out_proof.check(json.loads(manifest_path.read_text()), tmp_path)
    assert set(result["splits"]) == set(DIRECTED) and result["ok"]
    # one published byte of a training split changed to a held-out value: the re-check fails
    manifest = json.loads(manifest_path.read_text())
    train = next(s for s in manifest["content"]["splits"] if s["name"] == "train")
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pq.read_table(tmp_path / train["path"])
    genre = table.column("genre").to_pylist()
    genre[0] = "g4"
    pq.write_table(
        table.set_column(table.schema.get_field_index("genre"), "genre", pa.array(genre)),
        tmp_path / train["path"],
    )
    assert check_held_out_proof.main([str(manifest_path), str(tmp_path)]) == 1


async def test_the_export_manifests_carry_the_caveat_and_the_proofs(
    client: httpx.AsyncClient,
    driver: RealDriver,
    publisher: PublishDriver,
    data_dir: Path,
    operator_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review R1 F3: the caveat reached the draft and the publish, but not the two export
    manifests (a miForge set / TRL export and a reward bundle), which are dataset-version
    manifests too and already carry 015's ``session_data`` caveat (``with_session_caveat``). A
    miForge set of a version whose held-out split holds generated rows said nothing of them."""
    from src.core.database import sync_session_factory
    from src.models.version import Version
    from src.services.exports import reward_bundle
    from src.services.publishing import feature_seams as seams

    def valid(version_id: str, target_type: str, *, session: Any) -> seams.TrlFinding:
        return seams.TrlFinding(seams.CHECKED, True, [])

    monkeypatch.setattr(seams, "validate_trl", valid)
    version = await built(client, driver, data_dir, [ECHO, SPLIT], "exported")
    started = await client.post(
        "/api/v1/exports",
        json={"target": "miforge_set", "version_id": version["id"], "miforge_set_kind": "corpus"},
    )
    assert started.status_code == 202, started.text
    publisher.run_publish_tasks()
    row = (await client.get(f"/api/v1/exports/{started.json()['export_id']}")).json()
    assert row["status"] == "completed", row
    manifest = json.loads(
        (data_dir / "exports" / row["id"] / "midataworks-dataset-version.json").read_bytes()
    )
    assert_caveat_and_proofs(manifest, version)
    # the reward bundle's manifest (its builder, called as the export job calls it)
    with sync_session_factory()() as db:
        stored = db.get(Version, version["id"])
        assert stored is not None
        document = reward_bundle._manifest(  # noqa: SLF001
            db,
            stored,
            None,  # type: ignore[arg-type]
            {
                "rubric": {
                    "name": "r",
                    "version": "1",
                    "content_sha256": "0" * 64,
                    "style": "binary",
                },
                "judge_model_id": "judge-7b",
                "trl_version": "1.14.1",
            },
            [],
            None,
        )
    note = caveat(document)
    assert note is not None and {d["split"] for d in note["detail"]["splits"]} == set(DIRECTED)
    # a version without generated held-out rows: no caveat in the export either
    plain = await built(client, driver, data_dir, [SPLIT], "exported-plain")
    started = await client.post(
        "/api/v1/exports",
        json={"target": "miforge_set", "version_id": plain["id"], "miforge_set_kind": "corpus"},
    )
    publisher.run_publish_tasks()
    row = (await client.get(f"/api/v1/exports/{started.json()['export_id']}")).json()
    assert row["status"] == "completed", row
    manifest = json.loads(
        (data_dir / "exports" / row["id"] / "midataworks-dataset-version.json").read_bytes()
    )
    assert caveat(manifest) is None
    published = {s["name"]: s for s in manifest["content"]["splits"]}
    assert published["ood_genre"]["extensions"]["held_out_proof"]["passes"]


async def test_near_values_ride_the_proof_the_caveat_and_the_recheck(
    client: httpx.AsyncClient,
    driver: RealDriver,
    publisher: PublishDriver,
    data_dir: Path,
    operator_name: str,
    tmp_path: Path,
) -> None:
    """ENH-006 D42: training rows whose genre reads ``G4`` (held out: ``g4``) leave the build
    passing (exact matching decides) but are recorded as near values in the stored proof, the
    published ``extensions.held_out_proof`` and the ``generated_held_out`` caveat; the re-check
    script recomputes them from the published files alone."""
    from scripts import check_held_out_proof

    await store_token(client)
    rows = carriers("near") + carriers("upper", genres=("G4",), n=1)
    source = make_source(data_dir, {"train": rows}, repo_id="t/near")
    out = await run(client, driver, [ECHO, SPLIT], [src(source)], "near", target="untyped")
    assert out["status"] == "completed", out
    version = (await client.get(f"/api/v1/versions/{out['version_id']}")).json()
    stored = {s["name"]: s for s in version["splits"]}["ood_genre"]["proof"]
    assert stored["passes"] and stored["near_values_count"] == 1
    ((near,),) = [stored["near_values"]]
    assert near["column"] == "genre" and near["value"] == "g4"
    # G4 rows of gen-a and gen-b with their echoes (the gen-c ones go to ood_generator)
    assert near["training_value"] == "G4" and near["training_rows"] == 2 * 2
    build = await completed_build(client, publisher, version["id"], label_column=None)
    pub = await publish_and_run(client, publisher, version["id"], build["id"])
    assert pub["status"] == "published", pub["error"]
    files = publisher.hub.head_files(REPO)
    manifest = json.loads(files["midataworks-dataset-version.json"])
    published = {s["name"]: s for s in manifest["content"]["splits"]}
    assert published["ood_genre"]["extensions"]["held_out_proof"]["near_values"] == [near]
    note = caveat(manifest)
    assert note is not None and "case or whitespace" in note["message"]
    detail = {d["split"]: d for d in note["detail"]["splits"]}
    assert detail["ood_genre"]["near_values"] == [near]
    assert detail["ood_genre"]["near_values_count"] == 1
    assert detail["ood_generator"]["near_values"] == []
    for path, content in files.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_bytes(content)
    result = check_held_out_proof.check(manifest, tmp_path)
    assert result["ok"], result
    again = result["splits"]["ood_genre"]["recomputed"]
    assert again["near_values"] == [near] and again["near_values_count"] == 1
