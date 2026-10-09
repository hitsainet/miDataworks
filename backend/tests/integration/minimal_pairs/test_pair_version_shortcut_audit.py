"""D5 (live, 2026-10-08): D-3 refused EVERY minimal-pair detector set, with no remedy.

A pair version's label is ``pair_role`` (seed / counterpart), written by ``minimal_pair_join``. Six
columns predicted it at held-out 1.000 BY CONSTRUCTION — ``generation_run_id``,
``generation_record_index``, ``generation_model_id`` (``dw_generated_rows`` writes them on
counterparts only), ``pair_judge_verdict`` (the join), and the derived ``_dw_origin`` and
``_dw_source_id`` — and declaring ``_dw_origin`` as a label source column was refused. The live set
was built on a RE-SPLIT of the pair version (train / eval grouped by ``pair_id``), whose own steps
are only the split.

The audit now sets the pair-construction columns aside as ``pair_construction`` (found by walking
the version's lineage to the join, from the operators' manifests), reports them in D-3, and still
audits every column the construction did not write. Everything runs for real: 007's generation, the
build driver over the production registry, 005's judge run, 004's audit and 009's checks.
"""

from __future__ import annotations

from typing import Any

import httpx
import pyarrow as pa

from src.services.row_keys import compute_row_key
from tests.integration.minimal_pairs.test_minimal_pair_chain import (
    CHAINS,
    advance,
    chain_get,
    judge_job,
    load_judge,
    template_id,
    version_of,
)
from tests.support.generation_fixtures import Gen, make_version
from tests.support.operator_build_driver import RealDriver

ANIMALS = "cat dog fox owl bee ant elk yak emu gnu ram cod".split()
# "happy" -> "angry": the same length, so the length band carries no shortcut of its own
SEEDS = {f"the {a} is happy today": f"the {a} is angry today" for a in ANIMALS}
CONSTRUCTION = {
    "generation_run_id",
    "generation_record_index",
    "generation_model_id",
    "pair_judge_verdict",
    "_dw_origin",
    "_dw_source_id",
}
RUBRIC = {
    "style": "binary",
    "messages": [
        {
            "role": "user",
            "content": "Does ROW express joy?\nROW: {text}\nEND\nEnd with VERDICT: yes or no.",
        }
    ],
    "input_fields": ["text"],
    "parser": "verdict_line_v1",
    "allowed_verdicts": ["yes", "no"],
}


def answer(messages: list[dict[str, Any]], body: dict[str, Any]) -> str:
    content = str(messages[-1]["content"])
    if "ROW: " in content:
        row = content.split("ROW: ", 1)[1].split("\nEND", 1)[0]
        return f"Reading it.\nVERDICT: {'yes' if 'happy' in row else 'no'}"
    return SEEDS[content.split("Text:\n", 1)[1]]


def seed_version(*, leaking: bool, texts: list[str] | None = None) -> str:
    """The seed table: text only, or text plus a metadata ``topic`` that the counterparts will not
    carry (a seed-table column that leaks ``pair_role`` and that the construction did NOT write)."""
    rows: list[dict[str, Any]] = []
    train = list(SEEDS) if texts is None else texts
    for split, rows_text in (("train", train), ("test", ["held out happy row"])):
        for t in rows_text:
            row: dict[str, Any] = {"text": t}
            row["_dw_row_key"] = compute_row_key(row, ["text"])
            row.update(
                {
                    "_dw_occurrence": 0,
                    "_dw_split": split,
                    "_dw_origin": "source",
                    "_dw_source_id": None,
                    "_dw_source_locator": None,
                    "_dw_parent_keys": None,
                }
            )
            if leaking:
                row["topic"] = "animals"
            rows.append(row)
    fields = [
        ("text", pa.string()),
        ("_dw_row_key", pa.string()),
        ("_dw_occurrence", pa.int32()),
        ("_dw_split", pa.string()),
        ("_dw_origin", pa.string()),
        ("_dw_source_id", pa.string()),
        ("_dw_source_locator", pa.string()),
        ("_dw_parent_keys", pa.list_(pa.string())),
    ]
    roles = {"text": "content"}
    if leaking:
        fields.append(("topic", pa.string()))
        roles["topic"] = "metadata"
    return make_version(
        pa.Table.from_pylist(rows, schema=pa.schema(fields)),
        target_type="detector",
        roles=roles,
    )


async def pair_version(
    client: httpx.AsyncClient, gen: Gen, driver: RealDriver, seed: str, expected: int = len(SEEDS)
) -> str:
    gen.fake.gen_answer = answer
    rubric = await client.post("/api/v1/rubrics", json={"name": "mp/joy", "body": RUBRIC})
    assert rubric.status_code == 201, rubric.text
    body = {
        "input_version_id": seed,
        "text_column": "text",
        "seed_splits": ["train"],
        "sample_size": 50,
        "seed": 5,
        "respond_template_id": await template_id(client),
        "rubric_id": rubric.json()["id"],
        "flip_from": "yes",
        "flip_to": "no",
        "judge_seed": 1,
        "max_edit_words": 3,
    }
    started = await client.post(CHAINS, json=body)
    assert started.status_code == 202, started.text
    chain = started.json()
    assert gen.run_until_done(chain["stages"][0]["run_id"]).state == "completed"
    moved = await advance(chain["id"])
    assert moved.scope_job_id is not None and driver.run(moved.scope_job_id) == "completed"
    load_judge(gen)
    judging = await advance(chain["id"])
    assert judging.stage == "judge" and judging.judge_run_id is not None, judging.error
    from src.workers import label_run_tasks

    label_run_tasks.run_job(judge_job(judging.judge_run_id))
    waiting = await advance(chain["id"])
    assert waiting.pair_job_id is not None and driver.run(waiting.pair_job_id) == "completed"
    assert (await advance(chain["id"])).state == "completed"
    out = await chain_get(client, chain["id"])
    assert out["counts"]["pairs_verified"] == expected
    return str(out["pair_version_id"])


async def resplit(client: httpx.AsyncClient, driver: RealDriver, version_id: str) -> str:
    """The live final version: the pair version re-split to train / eval, grouped by pair_id."""
    source = (await client.get(f"/api/v1/versions/{version_id}")).json()
    recipe = await client.post(
        "/api/v1/recipes",
        json={
            "name": f"resplit-{version_id[:8]}",
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
            "dataset_id": source["dataset_id"],
            "inputs": [{"kind": "version", "version_id": version_id}],
            "recipe_revision_id": recipe.json()["head_revision_id"],
        },
    )
    assert build.status_code == 202, build.text
    job = build.json()["job_id"]
    assert driver.run(job) == "completed"
    return version_of(job)


async def d3_of(
    client: httpx.AsyncClient, version_id: str, name: str = "joy-minimal-pairs"
) -> dict[str, Any]:
    def role(kind: str, split: str) -> dict[str, Any]:
        return {
            "role": kind,
            "version_id": version_id,
            "split": split,
            "input_column": "text",
            "label_column": "pair_role",
            "label_mapping": {"seed": "positive", "counterpart": "negative"},
            "pair_column": "pair_id",
        }

    created = await client.post(
        "/api/v1/detector-sets",
        json={
            "name": name,
            "description": "Joy, as minimal pairs",
            "positive_meaning": "The text expresses joy",
            "roles": [role("train", "train"), role("id_test", "eval")],
        },
    )
    assert created.status_code == 201, created.text
    checks = await client.post(f"/api/v1/detector-sets/{created.json()['id']}/checks")
    assert checks.status_code == 200, checks.text
    return next(o for o in checks.json()["outcomes"] if o["code"] == "D-3")


def construction_excluded(d3: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {e["column"]: e for e in d3["details"]["excluded"] if e["reason"] == "pair_construction"}


async def test_a_resplit_pair_version_passes_d3_with_the_construction_reported(
    client: httpx.AsyncClient, gen: Gen, chain_env: RealDriver
) -> None:
    final = await resplit(
        client, chain_env, await pair_version(client, gen, chain_env, seed_version(leaking=False))
    )
    d3 = await d3_of(client, final)
    assert d3["outcome"] == "green", (d3["details"], d3["reason"])
    excluded = construction_excluded(d3)
    assert CONSTRUCTION <= set(excluded), sorted(excluded)
    assert excluded["pair_judge_verdict"]["source_operator"] == "minimal_pair_join@1"
    assert excluded["generation_run_id"]["source_operator"] == "dw_generated_rows@1"
    assert excluded["_dw_origin"]["source_operator"] == "minimal_pair_join@1"
    assert "pair_role" not in excluded, "the label is the label, not a construction column"
    for column in CONSTRUCTION:
        assert repr(column) in d3["reason"], (column, d3["reason"])
    assert "pair construction by" in d3["reason"]


async def test_a_seed_column_the_construction_did_not_write_still_refuses(
    client: httpx.AsyncClient, gen: Gen, chain_env: RealDriver
) -> None:
    final = await resplit(
        client, chain_env, await pair_version(client, gen, chain_env, seed_version(leaking=True))
    )
    d3 = await d3_of(client, final)
    assert d3["outcome"] == "refused", d3
    assert [w["column"] for w in d3["details"]["warnings"]] == ["topic"]
    assert CONSTRUCTION <= set(construction_excluded(d3))


async def test_an_audit_stored_before_the_exclusion_is_not_reused(
    client: httpx.AsyncClient, gen: Gen, chain_env: RealDriver, monkeypatch: Any
) -> None:
    """Audits are stored and found again by their parameters. One computed WITHOUT the exclusion
    (as every audit before this fix was) must not be served for a pair-construction label."""
    from src.services.curation import label_columns

    final = await resplit(
        client, chain_env, await pair_version(client, gen, chain_env, seed_version(leaking=False))
    )
    with monkeypatch.context() as m:
        m.setattr(label_columns, "pair_construction", lambda *a, **k: {})
        stale = await d3_of(client, final)
    assert stale["outcome"] == "refused", "the pre-fix audit warns on the construction"
    fresh = await d3_of(client, final, name="joy-minimal-pairs-2")
    assert fresh["outcome"] == "green", (fresh["details"], fresh["reason"])
