"""D7 (live, 2026-10-09): the publish record and checks C-4 / C-6 ignored the version's lineage.

Version ``15f07a79-…`` of ``highstakes-chat-v3-dw`` is a ``split`` over ten input VERSIONS, each a
minimal-pair chain's pair version: every counterpart is ``_dw_origin = generated`` and every pair
was verified by a judge label run. Its card draft said "No model labeler; labels, if any, came with
the sources", C-4 "No model labelled or generated rows in this version" and C-6 "No model labeler
produced labels in this version" — because the runs are bound on the ANCESTORS, and only the
version's own bindings were read.

The same shape here, for real: two chains (generation, scope build, judge run, pair build), then a
version whose only step is a split and whose inputs are the two pair versions. Read through the
routes the operator uses: ``GET /versions/{id}/card-draft`` and ``GET /versions/{id}/handoff-manifest``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import httpx
from sqlalchemy import text

from src.core.database import sync_session_factory
from src.models.version import Version
from tests.integration.minimal_pairs.test_minimal_pair_chain import version_of
from tests.integration.minimal_pairs.test_pair_version_shortcut_audit import (
    ANIMALS,
    pair_version,
    seed_version,
)
from tests.support.generation_fixtures import GEN_MODEL, JUDGE_MODEL, Gen
from tests.support.operator_build_driver import RealDriver

FIRST = [f"the {a} is happy today" for a in ANIMALS[:6]]
SECOND = [f"the {a} is happy today" for a in ANIMALS[6:]]


def sourced(version_id: str, source_id: str | None = None) -> str:
    """Give a fixture seed version the upload source it came from (its manifest's ``sources``),
    as an imported seed table has. Returns the source id."""
    from tests.support import db_factories

    with sync_session_factory()() as db:
        if source_id is None:
            source_id = db_factories.source(db, kind="upload", content_hash="a" * 64).id
            db.commit()
        version = db.get(Version, version_id)
        assert version is not None
        manifest = json.loads(version.manifest)
        manifest["sources"] = [{"source_id": source_id}]
        body = json.dumps(manifest, sort_keys=True).encode()
        db.execute(text("ALTER TABLE dw_versions DISABLE TRIGGER dw_versions_immutable"))
        db.execute(text("ALTER TABLE dw_versions DISABLE TRIGGER dw_versions_manifest_hash"))
        version.manifest = body
        version.manifest_sha256 = hashlib.sha256(body).hexdigest()
        db.commit()
        db.execute(text("ALTER TABLE dw_versions ENABLE TRIGGER dw_versions_immutable"))
        db.execute(text("ALTER TABLE dw_versions ENABLE TRIGGER dw_versions_manifest_hash"))
        db.commit()
    return source_id


async def resplit_over(client: httpx.AsyncClient, driver: RealDriver, inputs: list[str]) -> str:
    """The live final version: a split whose inputs are several pair versions."""
    with sync_session_factory()() as db:
        first = db.get(Version, inputs[0])
        assert first is not None
        dataset_id = str(first.dataset_id)
    recipe = await client.post(
        "/api/v1/recipes",
        json={
            "name": "resplit-pairs",
            "body": {
                "format": "dw.recipe/v1",
                "steps": [
                    {
                        "operator": "split",
                        "version": "1.0.0",
                        "params": {
                            "split_names": ["train", "eval"],
                            "split_fractions": [0.5, 0.5],
                            "held_out": [],
                            "group_column": "pair_id",
                        },
                    }
                ],
            },
        },
    )
    assert recipe.status_code == 201, recipe.text
    build = await client.post(
        "/api/v1/versions",
        json={
            "dataset_id": dataset_id,
            "inputs": [{"kind": "version", "version_id": v} for v in inputs],
            "recipe_revision_id": recipe.json()["head_revision_id"],
        },
    )
    assert build.status_code == 202, build.text
    job = build.json()["job_id"]
    assert driver.run(job) == "completed"
    return version_of(job)


async def publish_build(
    client: httpx.AsyncClient, driver: RealDriver, version_id: str
) -> dict[str, Any]:
    from src.workers import publish_tasks

    response = await client.post(
        f"/api/v1/versions/{version_id}/publish-builds", json={"label_column": "pair_role"}
    )
    assert response.status_code in (200, 202), response.text
    pending = [a for n, a in driver.sent if n == "midataworks.publish.build"]
    driver.sent = [(n, a) for n, a in driver.sent if n != "midataworks.publish.build"]
    for args in pending:
        publish_tasks.run_build_job(args[0])
    got = await client.get(f"/api/v1/publish-builds/{response.json()['build_id']}")
    assert got.json()["status"] == "completed", got.json()
    data: dict[str, Any] = got.json()
    return data


async def live_shape(client: httpx.AsyncClient, gen: Gen, driver: RealDriver) -> str:
    seed_a, seed_b = seed_version(leaking=False, texts=FIRST), seed_version(
        leaking=False, texts=SECOND
    )
    upload = sourced(seed_a)
    sourced(seed_b, upload)  # both seed tables came from one upload, as live
    first = await pair_version(client, gen, driver, seed_a, expected=len(FIRST))
    assert gen.fake.resident is not None
    gen.fake.resident = {**gen.fake.resident, "name": GEN_MODEL}  # the generator again
    second = await pair_version(client, gen, driver, seed_b, expected=len(SECOND))
    return await resplit_over(client, driver, [first, second])


def check_line(record: str, check: str) -> str:
    return next(line for line in record.splitlines() if line.startswith(f"- {check} "))


async def test_a_resplit_over_pair_versions_names_its_generator_and_judge(
    client: httpx.AsyncClient, gen: Gen, chain_env: RealDriver, monkeypatch: Any
) -> None:
    from src.workers import publish_tasks

    monkeypatch.setattr(publish_tasks, "emit", chain_env.emit)
    monkeypatch.setattr(publish_tasks, "_next_jobs", lambda: None)
    final = await live_shape(client, gen, chain_env)
    with sync_session_factory()() as db:
        row = db.get(Version, final)
        assert row is not None and row.bindings == [], "the final version binds nothing itself"
    build = await publish_build(client, chain_env, final)
    pairs = len(FIRST) + len(SECOND)
    assert sum(f["rows"] for f in build["files"]) == 2 * pairs

    draft = await client.get(f"/api/v1/versions/{final}/card-draft")
    assert draft.status_code == 200, draft.text
    record = draft.json()["record_markdown"]

    # C-4: the generated rows are counted from the rows, and both models are named
    c4 = check_line(record, "C-4")
    assert c4.startswith("- C-4 amber: No terms note is recorded for"), c4
    assert f"{pairs:,} generated row(s) from {GEN_MODEL}" in c4, c4
    assert f"labels from judge {JUDGE_MODEL}" in c4, c4
    # C-6: each chain's judge run is a labeler whose labels the version carries (two
    # fingerprints: the chains pinned rubric versions 1 and 2), with no calibration
    c6 = check_line(record, "C-6")
    assert c6.startswith("- C-6 amber: 2 labeler(s) lack a valid calibration record"), c6
    assert JUDGE_MODEL in c6, c6

    # Labels: the judge, its rubric and both runs
    labels = record.split("### Labels", 1)[1].split("###", 1)[0]
    assert "No model labeler" not in labels
    assert labels.count(f"- judge `{JUDGE_MODEL}`") == 2, labels
    # each chain created its rubric, so they are versions 1 and 2 of one name
    assert "rubric mp/joy v1" in labels and "rubric mp/joy v2" in labels, labels
    assert labels.count("`lr_") == 2, labels
    # Generated rows: the count and the generator of each run
    generated = record.split("### Generated rows", 1)[1].split("###", 1)[0]
    assert f"{pairs:,} of {2 * pairs:,} row(s) were generated by a model." in generated
    assert generated.count(f"`{GEN_MODEL}`") == 2, generated
    # Rows dropped by step says what it covers and names the ancestors
    dropped = record.split("### Rows dropped by step", 1)[1].split("###", 1)[0]
    assert "covers this version's own steps only" in dropped, dropped
    with sync_session_factory()() as db:
        inputs = [
            str(r[0])
            for r in db.execute(
                __import__("sqlalchemy").text(
                    "SELECT input_version_id FROM dw_version_inputs WHERE version_id = :v"
                ),
                {"v": final},
            ).all()
        ]
    assert len(inputs) == 2 and all(f"(`{v}`)" in dropped for v in inputs), dropped
    # Sources: the upload both seed tables came from, reached through the lineage, listed once
    sources = record.split("### Sources", 1)[1].split("###", 1)[0]
    assert sources.count("- upload `upload.parquet`") == 1, sources

    manifest = (await client.get(f"/api/v1/versions/{final}/handoff-manifest")).json()
    labelers = manifest["content"]["labelers"]
    assert len(labelers) == 2
    for labeler in labelers:
        assert labeler["role"] == "judge" and labeler["model_id"] == JUDGE_MODEL
        assert labeler["extensions"]["rubric"]["name"] == "mp/joy"
    lineage = manifest["extensions"]["lineage"]
    assert lineage["generated_rows"] == pairs
    assert {g["model_id"] for g in lineage["generators"]} == {GEN_MODEL}
    assert len(lineage["generators"]) == 2
    assert set(inputs) <= {a["version_id"] for a in lineage["ancestor_versions"]}
