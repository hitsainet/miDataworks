"""The minimal-pair chain end to end (009 FTASKS 15.1 - 15.3; operator decision 2026-10-07).

Every stage is the REAL piece: 007's generation run and worker against a fake miLLM on a loopback
port, 002's build through 003's executor with the PRODUCTION operator registry, and 005's judge run
through its real task body. Only the broker is replaced. The judge reads a rubric pinned by ID, and
the fake answers it from the row text, so verified and unverified flips are decided by the judge's
verdicts — never by a fixture that agrees with the code.
"""

from __future__ import annotations

from typing import Any

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import select, text

from src.core.database import async_session_factory, sync_session_factory
from src.core.storage import resolve_under_data_dir
from src.models.generation import GenerationRecord
from src.models.job import Job
from src.models.label_run import LabelRunJob
from src.models.minimal_pair_chain import MinimalPairChain
from src.models.version import Version, VersionBuild
from src.services.detector_sets import minimal_pair_chain as svc
from src.services.detector_sets.minimal_pairs import edit_size
from src.services.row_keys import compute_row_key
from tests.support.generation_fixtures import GEN_MODEL, JUDGE_MODEL, Gen, make_version, set_role
from tests.support.operator_build_driver import RealDriver

CHAINS = "/api/v1/minimal-pair-chains"
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

SEEDS = {
    "the cat is happy today": "the cat is sad today",  # verified
    "the dog is happy now": "the dog is sad now",  # verified
    "the fox is happy here": "the fox is very happy here",  # the judge still reads joy
    "the owl is gloomy there": "the owl is glum there",  # the seed was never joyful
    "the bee is happy still": "the bee is happy still",  # no edit at all
    "the ant is happy again": "a much longer sentence about ants that march off in a line",
}
HELD_OUT = ("held out happy row",)


def detector_table(train: list[str], test: tuple[str, ...] = HELD_OUT) -> pa.Table:
    rows: list[dict[str, Any]] = []
    for split, texts in (("train", train), ("test", list(test))):
        for t in texts:
            row: dict[str, Any] = {"text": t, "label": "joy" if "happy" in t else "none"}
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
            rows.append(row)
    return pa.Table.from_pylist(
        rows,
        schema=pa.schema(
            [
                ("text", pa.string()),
                ("label", pa.string()),
                ("_dw_row_key", pa.string()),
                ("_dw_occurrence", pa.int32()),
                ("_dw_split", pa.string()),
                ("_dw_origin", pa.string()),
                ("_dw_source_id", pa.string()),
                ("_dw_source_locator", pa.string()),
                ("_dw_parent_keys", pa.list_(pa.string())),
            ]
        ),
    )


def detector_version(train: list[str] | None = None, *, held_out: bool = True) -> str:
    return make_version(
        detector_table(list(SEEDS) if train is None else train),
        target_type="detector",
        roles={"text": "content", "label": "metadata"},
        held_out=held_out,
    )


def answer(messages: list[dict[str, Any]], body: dict[str, Any]) -> str:
    """The generator answers from SEEDS; the judge answers from the ROW text alone."""
    content = str(messages[-1]["content"])
    if "ROW: " in content:
        row = content.split("ROW: ", 1)[1].split("\nEND", 1)[0]
        return f"Reading it.\nVERDICT: {'yes' if 'happy' in row else 'no'}"
    seed = content.split("Text:\n", 1)[1]
    return SEEDS[seed]


async def template_id(client: httpx.AsyncClient) -> str:
    listed = await client.get("/api/v1/generation-templates", params={"kind": "respond"})
    (found,) = [t for t in listed.json()["items"] if t["name"] == "minimal-pair-v1"]
    return str(found["id"])


async def rubric_id(client: httpx.AsyncClient, name: str = "mp/joy") -> str:
    created = await client.post("/api/v1/rubrics", json={"name": name, "body": RUBRIC})
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


async def chain_body(client: httpx.AsyncClient, version: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "input_version_id": version,
        "text_column": "text",
        "seed_splits": ["train"],
        "sample_size": 50,
        "seed": 5,
        "respond_template_id": await template_id(client),
        "rubric_id": await rubric_id(client),
        "flip_from": "yes",
        "flip_to": "no",
        "judge_seed": 1,
        "max_edit_words": 3,
    }
    body.update(overrides)
    return body


async def advance(chain_id: str) -> MinimalPairChain:
    async with async_session_factory()() as db:
        chain = await svc.advance(db, chain_id)
        db.expunge(chain)
        return chain


async def chain_get(client: httpx.AsyncClient, chain_id: str) -> dict[str, Any]:
    response = await client.get(f"{CHAINS}/{chain_id}")
    assert response.status_code == 200, response.text
    return dict(response.json())


def version_of(job_id: str) -> str:
    with sync_session_factory()() as db:
        build = db.get(VersionBuild, job_id)
        assert build is not None and build.version_id is not None
        return str(build.version_id)


def rows_of(version_id: str) -> list[dict[str, Any]]:
    with sync_session_factory()() as db:
        version = db.get(Version, version_id)
        assert version is not None
        splits = list(version.splits)
    out: list[dict[str, Any]] = []
    for split in splits:
        out.extend(pq.read_table(resolve_under_data_dir(split["path"])).to_pylist())
    return out


def judge_job(run_id: str) -> str:
    with sync_session_factory()() as db:
        return str(
            db.execute(
                select(LabelRunJob.job_id)
                .where(LabelRunJob.label_run_id == run_id)
                .order_by(LabelRunJob.seq.desc())
            )
            .scalars()
            .first()
        )


def judge_chats(gen: Gen) -> list[Any]:
    return [r for r in gen.chats() if "ROW: " in str(r.body["messages"][-1]["content"])]


def load_judge(gen: Gen) -> None:
    """The operator loads the judge model in miLLM (miDataworks never loads one)."""
    assert gen.fake.resident is not None
    gen.fake.resident = {**gen.fake.resident, "name": JUDGE_MODEL}


async def run_chain_to_judge(client: httpx.AsyncClient, gen: Gen, driver: RealDriver) -> str:
    """Start a chain and take it through generation and the scope build (judge model not loaded)."""
    gen.fake.gen_answer = answer
    version = detector_version()
    response = await client.post(CHAINS, json=await chain_body(client, version))
    assert response.status_code == 202, response.text
    chain = response.json()
    assert chain["state"] == "running" and chain["stage"] == "generate"
    assert [s["stage"] for s in chain["stages"]] == ["generate", "scope", "judge", "pair"]
    run_id = chain["stages"][0]["run_id"]
    assert run_id and chain["stages"][1]["state"] == "pending"
    assert gen.run_until_done(run_id).state == "completed"
    moved = await advance(chain["id"])
    assert moved.stage == "scope" and moved.scope_job_id is not None
    assert driver.run(moved.scope_job_id) == "completed"
    return str(chain["id"])


async def test_the_chain_keeps_only_verified_flips_with_provenance(
    client: httpx.AsyncClient, gen: Gen, chain_env: RealDriver
) -> None:
    chain_id = await run_chain_to_judge(client, gen, chain_env)

    # 1. The judge model is not loaded: the judge stage refuses to START and the chain stops,
    #    naming the stage. Nothing was judged.
    stopped = await advance(chain_id)
    assert stopped.state == "failed" and stopped.failed_stage == "judge"
    assert stopped.error is not None and stopped.error["code"] == "MODEL_NOT_LOADED"
    assert stopped.judge_run_id is None and judge_chats(gen) == []
    seen = await chain_get(client, chain_id)
    assert seen["resumable"] is True and seen["stages"][1]["state"] == "completed"

    # 2. The operator loads the judge and resumes: the judge run starts over the SCOPE version.
    load_judge(gen)
    resumed = await client.post(f"{CHAINS}/{chain_id}/resume")
    assert resumed.status_code == 200, resumed.text
    body = resumed.json()
    assert body["state"] == "running" and body["stage"] == "judge"
    judge_run = body["stages"][2]["run_id"]
    assert judge_run is not None
    scope_version = body["stages"][1]["version_id"]
    scope_rows = rows_of(scope_version)
    # four pairs reach the judge: no_edit and edit_too_large never do, nor the held-out row
    assert len(scope_rows) == 8
    assert sorted(r["_dw_origin"] for r in scope_rows) == ["generated"] * 4 + ["source"] * 4
    from src.workers import label_run_tasks

    label_run_tasks.run_job(judge_job(judge_run))
    assert len(judge_chats(gen)) == 8, "the judge reads exactly the four pairs, both rows each"
    assert {c.body["model"] for c in judge_chats(gen)} == {JUDGE_MODEL}

    # 3. The pair build: verified flips only, with pair_id and provenance.
    waiting = await advance(chain_id)
    assert waiting.stage == "pair" and waiting.pair_job_id is not None
    assert chain_env.run(waiting.pair_job_id) == "completed"
    done = await advance(chain_id)
    assert done.state == "completed" and done.stage == "done"
    out = await chain_get(client, chain_id)
    assert out["pair_version_id"] == version_of(waiting.pair_job_id)
    assert out["counts"]["pairs_judged"] == 4
    assert out["counts"]["pairs_verified"] == 2
    assert out["counts"]["unverified_by_reason"] == {
        "flip_not_verified": 1,
        "seed_not_flip_from": 1,
    }
    assert out["counts"]["scope_dropped"] == {
        "edit_too_large": 1,
        "no_edit": 1,
        "not_in_run": 1,
        "seed_without_counterpart": 2,
    }

    rows = rows_of(out["pair_version_id"])
    assert len(rows) == 4
    by_pair: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_pair.setdefault(row["pair_id"], []).append(row)
    assert len(by_pair) == 2
    with sync_session_factory()() as db:
        records = {
            r.seed_row_key: r
            for r in db.execute(
                select(GenerationRecord).where(
                    GenerationRecord.run_id == out["stages"][0]["run_id"]
                )
            ).scalars()
        }
    for pid, members in by_pair.items():
        seed = next(m for m in members if m["pair_role"] == "seed")
        counterpart = next(m for m in members if m["pair_role"] == "counterpart")
        assert pid == seed["_dw_row_key"] == counterpart["pair_seed_row_key"]
        assert seed["_dw_origin"] == "source" and counterpart["_dw_origin"] == "generated"
        assert SEEDS[seed["text"]] == counterpart["text"]
        assert (seed["pair_judge_verdict"], counterpart["pair_judge_verdict"]) == ("yes", "no")
        record = records[pid]
        for member in members:
            assert member["pair_generator_model_id"] == GEN_MODEL == record.model_id
            assert member["pair_generator_revision"] == record.model_revision
            assert member["pair_steering_state"] == (record.reported_steering or "not reported")
            assert member["pair_judge_model_id"] == JUDGE_MODEL
            assert member["pair_judge_run_id"] == judge_run
            assert member["pair_judge_rubric"] == "mp/joy@1"
            assert member["pair_edit_words"] == 1
            assert member["pair_edit_chars"] == edit_size(seed["text"], counterpart["text"]).chars
    kept_texts = sorted(r["text"] for r in rows)
    assert kept_texts == sorted(
        [
            "the cat is happy today",
            "the cat is sad today",
            "the dog is happy now",
            "the dog is sad now",
        ]
    )


async def test_a_cancelled_stage_stops_the_chain_and_resume_continues_it(
    client: httpx.AsyncClient, gen: Gen, chain_env: RealDriver
) -> None:
    gen.fake.gen_answer = answer
    version = detector_version(["the cat is happy today"])
    response = await client.post(CHAINS, json=await chain_body(client, version))
    chain = response.json()
    run_id = chain["stages"][0]["run_id"]
    cancelled = await client.post(f"/api/v1/generation-runs/{run_id}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["state"] == "cancelled"
    stopped = await advance(chain["id"])
    assert stopped.state == "failed" and stopped.failed_stage == "generate"
    assert stopped.error is not None and stopped.error["stage"] == "generate"
    assert stopped.scope_job_id is None, "a failed stage starts nothing after it"
    with sync_session_factory()() as db:
        builds = db.execute(select(Job).where(Job.kind == "version_build")).scalars().all()
    assert builds == [] and chain_env.steps == []
    resumed = await client.post(f"{CHAINS}/{chain['id']}/resume")
    assert resumed.status_code == 200 and resumed.json()["state"] == "running"
    assert gen.run_until_done(run_id).state == "completed"
    # Beat's own task body moves it on (its own engine and event loop, as in a worker)
    import asyncio

    from src.workers import minimal_pair_tasks

    ticked = await asyncio.to_thread(minimal_pair_tasks.advance_minimal_pair_chains)
    assert ticked == {chain["id"]: "running:scope"}
    seen = await chain_get(client, chain["id"])
    assert seen["stage"] == "scope" and seen["stages"][1]["job_id"] is not None


async def test_cancel_stops_the_live_stage_through_its_owner(
    client: httpx.AsyncClient, gen: Gen, chain_env: RealDriver
) -> None:
    gen.fake.gen_answer = answer
    version = detector_version(["the cat is happy today"])
    chain = (await client.post(CHAINS, json=await chain_body(client, version))).json()
    response = await client.post(f"{CHAINS}/{chain['id']}/cancel")
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "cancelled"
    assert response.json()["stages"][0]["state"] == "cancelled"
    again = await client.post(f"{CHAINS}/{chain['id']}/cancel")
    assert again.status_code == 409 and again.json()["error"]["code"] == "CHAIN_NOT_CANCELLABLE"
    listed = await client.get(CHAINS, params={"state": "cancelled"})
    assert [c["id"] for c in listed.json()["items"]] == [chain["id"]]


# --- refusals before anything runs (FTASKS 15.2) ---------------------------------------------


def nothing_written() -> None:
    with sync_session_factory()() as db:
        for table in ("dw_minimal_pair_chains", "dw_generation_runs", "dw_label_runs"):
            assert (
                db.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
            ), table  # noqa: S608
        kinds = db.execute(text("SELECT kind FROM dw_jobs")).scalars().all()
        assert not set(kinds) & {"generation_run", "label_run", "version_build"}, kinds


async def test_refusals_come_before_anything_runs(
    client: httpx.AsyncClient, gen: Gen, chain_env: RealDriver
) -> None:
    version = detector_version()
    body = await chain_body(client, version)

    # a seed split that is held out (FR-009.64, R-03.38)
    held = await client.post(CHAINS, json={**body, "seed_splits": ["test"]})
    assert held.status_code == 422 and held.json()["error"]["code"] == "HELD_OUT_SEED"

    # a version with no held-out split at all (007 FR-007.35)
    no_split = detector_version(held_out=False)
    missing = await client.post(CHAINS, json={**body, "input_version_id": no_split})
    assert missing.status_code == 409 and missing.json()["error"]["code"] == "HELD_OUT_MISSING"

    # a flip verdict the rubric cannot give
    unknown = await client.post(CHAINS, json={**body, "flip_to": "maybe"})
    assert unknown.status_code == 422
    assert unknown.json()["error"]["code"] == "FLIP_VERDICT_UNKNOWN"

    # the judge is the generator (FR-009.62, T-35)
    set_role("judge", gen.base_url, GEN_MODEL)
    same = await client.post(CHAINS, json=body)
    assert same.status_code == 422 and same.json()["error"]["code"] == "JUDGE_IS_GENERATOR"
    planned = await client.post(f"{CHAINS}/plan", json=body)
    assert planned.json()["error"]["code"] == "JUDGE_IS_GENERATOR"

    # no judge at all: nothing could verify a flip
    with sync_session_factory()() as db:
        db.execute(text("DELETE FROM dw_endpoint_roles WHERE role = 'judge'"))
        db.commit()
    none = await client.post(CHAINS, json=body)
    assert none.status_code == 409 and none.json()["error"]["code"] == "JUDGE_UNCONFIGURED"

    nothing_written()
    assert gen.chats() == [], "no request reached the endpoint"


async def test_the_plan_names_every_stage_and_writes_nothing(
    client: httpx.AsyncClient, gen: Gen, chain_env: RealDriver
) -> None:
    version = detector_version()
    planned = await client.post(f"{CHAINS}/plan", json=await chain_body(client, version))
    assert planned.status_code == 200, planned.text
    out = planned.json()
    assert out["stages"] == ["generate", "scope", "judge", "pair"]
    assert out["judge"]["model_id"] == JUDGE_MODEL and out["judge"]["rubric_ref"] == "mp/joy@1"
    assert out["judge"]["field_map"] == {"text": "text"}
    assert out["generation"]["mode"] == "minimal_pairs"
    assert out["generation"]["seed_rows_selected"] == len(SEEDS)
    assert out["judge_rows_at_most"] == 2 * len(SEEDS)
    nothing_written()


async def test_a_running_chain_keeps_its_seed_version_from_deletion(
    client: httpx.AsyncClient, gen: Gen, chain_env: RealDriver
) -> None:
    gen.fake.gen_answer = answer
    version = detector_version(["the cat is happy today"])
    chain = (await client.post(CHAINS, json=await chain_body(client, version))).json()
    await client.post(f"/api/v1/generation-runs/{chain['stages'][0]['run_id']}/cancel")
    await client.post(f"{CHAINS}/{chain['id']}/cancel")
    # stopped: the version may go (the chain row keeps its id as evidence)
    from src.services.detector_sets.delete_guards import version_in_running_chain

    async with async_session_factory()() as db:
        assert await version_in_running_chain(db, version) is None
        row = await db.get(MinimalPairChain, chain["id"])
        assert row is not None
        row.state = "running"
        await db.commit()
        found = await version_in_running_chain(db, version)
    assert found is not None and found.code == "version_in_use"
    assert found.details == {"chain_id": chain["id"]}


# --- every stage's failure stops the chain at THAT stage (controls C20, C21, C23) ---------------


async def test_a_cancelled_judge_run_stops_the_chain_at_judge_and_resume_resumes_it(
    client: httpx.AsyncClient, gen: Gen, chain_env: RealDriver
) -> None:
    chain_id = await run_chain_to_judge(client, gen, chain_env)
    load_judge(gen)
    started = await advance(chain_id)
    assert started.stage == "judge" and started.judge_run_id is not None
    judge_run = started.judge_run_id
    cancelled = await client.post(f"/api/v1/label-runs/{judge_run}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    stopped = await advance(chain_id)
    assert stopped.state == "failed" and stopped.failed_stage == "judge"
    assert stopped.pair_job_id is None, "a failed stage starts nothing after it"
    before = judge_job(judge_run)
    resumed = await client.post(f"{CHAINS}/{chain_id}/resume")
    assert resumed.status_code == 200 and resumed.json()["state"] == "running"
    assert resumed.json()["stages"][2]["run_id"] == judge_run, "the same judge run continues"
    assert judge_job(judge_run) != before, "through 005's own resume: a new job"


async def test_a_failed_build_stops_the_chain_at_its_stage_and_resume_builds_again(
    client: httpx.AsyncClient, gen: Gen, chain_env: RealDriver
) -> None:
    gen.fake.gen_answer = answer
    version = detector_version()
    chain = (await client.post(CHAINS, json=await chain_body(client, version))).json()
    assert gen.run_until_done(chain["stages"][0]["run_id"]).state == "completed"
    moved = await advance(chain["id"])
    assert moved.stage == "scope" and moved.scope_job_id is not None
    first_job = moved.scope_job_id
    cancelled = await client.post(f"/api/v1/jobs/{first_job}/cancel", json={"reason": "test"})
    assert cancelled.status_code == 202 and cancelled.json()["job"]["status"] == "cancelled"
    stopped = await advance(chain["id"])
    assert stopped.state == "failed" and stopped.failed_stage == "scope"
    assert stopped.error is not None and stopped.error["details"]["ref"] == f"build {first_job}"
    assert stopped.judge_run_id is None
    resumed = await client.post(f"{CHAINS}/{chain['id']}/resume")
    assert resumed.status_code == 200, resumed.text
    again = resumed.json()["stages"][1]["job_id"]
    assert again is not None and again != first_job, "002 builds the same request again"
    assert chain_env.run(again) == "completed"


async def test_an_agents_judge_run_over_the_threshold_waits_for_an_operator(
    client: httpx.AsyncClient, gen: Gen, chain_env: RealDriver, monkeypatch: Any
) -> None:
    from src.core.config import get_settings

    monkeypatch.setattr(get_settings(), "agent_label_row_threshold", 2)
    gen.fake.gen_answer = answer
    version = detector_version()
    agent = {"X-Dataworks-Agent": "agent:pairs-bot"}
    body = await chain_body(client, version)
    response = await client.post(CHAINS, json=body, headers=agent)
    assert response.status_code == 202, response.text
    chain = response.json()
    assert chain["started_by"] == "agent:pairs-bot" and chain["acting_origin"] == "agent"
    assert gen.run_until_done(chain["stages"][0]["run_id"]).state == "completed"
    moved = await advance(chain["id"])
    assert moved.scope_job_id is not None and chain_env.run(moved.scope_job_id) == "completed"
    load_judge(gen)
    stopped = await advance(chain["id"])
    assert stopped.state == "failed" and stopped.failed_stage == "judge"
    assert stopped.error is not None and stopped.error["code"] == "APPROVAL_NEEDED"
    assert stopped.judge_run_id is None and judge_chats(gen) == []
    resumed = await client.post(f"{CHAINS}/{chain['id']}/resume")  # the operator
    assert resumed.status_code == 200, resumed.text
    out = resumed.json()
    assert out["acting_origin"] == "operator" and out["stages"][2]["run_id"] is not None
