"""Carrier generation runs (017 FTASKS 5.1 - 5.11; FR-017.14 - FR-017.20; TD-017.6 - TD-017.13).

A plan version built by ``carrier_plan@1`` through the real orchestrator, then real generation runs
against the fake miLLM on loopback (every request recorded: payload and count asserted), candidate
versions built from them through the real build path. Both fixture behaviours: fixture-alpha's slot
carriers and fixture-beta's instructed carriers.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pyarrow.parquet as pq
import pytest
from sqlalchemy import select, update

from src.core.config import get_settings
from src.core.database import sync_session_factory
from src.models.generation import GenerationRun, GenerationRunJob
from src.models.job import Job
from src.models.model_lease import ModelLease
from src.services.generation import record_store
from tests.integration.behaviour.test_carrier_plan_build import plan_version
from tests.support.generation_fixtures import GEN_MODEL, REVISION, Gen
from tests.support.mcp_live import call, live_mcp
from tests.support.operator_build_driver import RealDriver

RUNS = "/api/v1/generation-runs"
TEMPLATES = "/api/v1/generation-templates"
SLOT = re.compile(r"\[\[SLOT-[0-9A-F]+\]\]")
CARRIER = re.compile(r"Carrier ([0-9a-f]+)")
SLOT_PROMPT = (
    "Carrier {carrier_id}. Write a {length_band} {genre} text with {framing} framing; put {slot} "
    "where the value goes ({placement})."
)
INSTRUCTED_SYSTEM = "You write {genre} texts."
INSTRUCTED_PROMPT = (
    "Carrier {carrier_id}: a {length_band} text that {framing}; complied={planned_complied}; "
    "warned={planned_warned}."
)


async def template(client: httpx.AsyncClient, name: str, body: dict[str, Any], **kw: Any) -> str:
    response = await client.post(
        TEMPLATES, json={"name": name, "kind": "respond", "body": body, **kw}
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def template_map(
    client: httpx.AsyncClient, plan: dict[str, Any], body: dict[str, Any], tag: str
) -> dict[str, Any]:
    genres = plan["behaviour_configuration"]["vocabulary"]["genres"]
    return {
        "column": "genre",
        "templates": {g: await template(client, f"{tag}-{g}", body) for g in genres},
    }


def run_body(plan: dict[str, Any], mapping: dict[str, Any], mode: str, **over: Any) -> dict:
    body = {
        "mode": mode,
        "input_version_id": plan["id"],
        "prompt_column": "text",
        "seed_splits": ["train"],
        "target_type": "detector",
        "generator_model_id": GEN_MODEL,
        "template_map": mapping,
        "behaviour_key": plan["behaviour_definition"]["key"],
        "seed": 5,
    }
    body.update(over)
    return body


def plan_rows(data_dir: Path, plan: dict[str, Any]) -> list[dict[str, Any]]:
    train = next(s for s in plan["splits"] if s["name"] == "train")
    return list(pq.read_table(data_dir / train["path"]).to_pylist())


def model_route(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if r["generator"] != "template"]


def carrier_of(chat: Any) -> str:
    content = " ".join(str(m.get("content")) for m in chat.body["messages"])
    found = CARRIER.search(content)
    assert found, content
    return found.group(1)


def slot_answer(messages: list[dict[str, Any]], body: dict[str, Any]) -> str:
    """By the carrier id's first hex digit: 0-3 drop the slot, 4-7 repeat it, the rest keep one."""
    text = " ".join(str(m.get("content")) for m in messages)
    token = SLOT.search(text)
    carrier = CARRIER.search(text)
    assert token and carrier, text
    digit = int(carrier.group(1)[0], 16)
    if digit < 4:
        return "A carrier that forgot its slot."
    if digit < 8:
        return f"Twice {token.group(0)} and {token.group(0)}."
    return f"Before {token.group(0)} after."


def expected_slots(carrier_id: str) -> str:
    digit = int(carrier_id[0], 16)
    return "slot_missing" if digit < 4 else "slot_repeated" if digit < 8 else "generated"


async def started(client: httpx.AsyncClient, body: dict[str, Any]) -> dict[str, Any]:
    response = await client.post(RUNS, json=body)
    assert response.status_code == 202, response.text
    return dict(response.json())


async def candidate(client: httpx.AsyncClient, driver: RealDriver, run_id: str) -> dict:
    from src.models.version import VersionBuild

    response = await client.post(f"{RUNS}/{run_id}/candidate-build", json={"seed": 3})
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    status = driver.run(job_id)
    with sync_session_factory()() as db:
        job = db.get(Job, job_id)
        assert status == "completed", (job.error if job else None, job.result if job else None)
    with sync_session_factory()() as db:
        row = db.get(VersionBuild, job_id)
        assert row is not None and row.version_id is not None
        version_id = str(row.version_id)
    got = await client.get(f"/api/v1/versions/{version_id}")
    assert got.status_code == 200, got.text
    return dict(got.json())


def version_rows(data_dir: Path, version: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for split in version["splits"]:
        out.extend(pq.read_table(data_dir / split["path"]).to_pylist())
    return out


async def test_slot_carriers_keep_one_slot_rows_and_count_the_drops(
    client: httpx.AsyncClient, gen: Gen, driver: RealDriver, data_dir: Path
) -> None:
    """FR-017.14 (fixture-alpha): every model-route plan row once, in key order; the run's own
    generator sent as ``model``; a carrier with no slot or two is dropped and counted."""
    plan = await plan_version(client, driver, data_dir, "fixture-alpha", 120)
    rows = model_route(plan_rows(data_dir, plan))
    assert 0 < len(rows) < 120, "the templated minority is left to the synthesiser"
    mapping = await template_map(client, plan, {"prompt": SLOT_PROMPT}, "alpha-slot")
    gen.fake.gen_answer = slot_answer
    run = await started(client, run_body(plan, mapping, "carrier_slot"))
    assert run["generator_model_id"] == GEN_MODEL and SLOT.fullmatch(run["slot_token"])
    assert run["sample_size"] == len(rows) and run["requests_in_flight"] == 1
    assert run["template_map"]["column"] == "genre"
    done = gen.run_until_done(run["id"])
    assert done.state == "completed", done.error
    chats = gen.chats()
    assert len(chats) == len(rows), "one request per model-route plan row"
    assert {c.body["model"] for c in chats} == {GEN_MODEL}
    assert sorted(carrier_of(c) for c in chats) == sorted(r["carrier_id"] for r in rows)
    assert all(run["slot_token"] in json.dumps(c.body["messages"]) for c in chats)
    seeds = record_store.read_seeds(run["id"])
    assert seeds is not None
    assert seeds.column("row_key").to_pylist() == sorted(r["_dw_row_key"] for r in rows)
    want = Counter(expected_slots(r["carrier_id"]) for r in rows)
    assert want["slot_missing"] and want["slot_repeated"] and want["generated"]
    assert done.counts["reason:slot_missing"] == want["slot_missing"]
    assert done.counts["reason:slot_repeated"] == want["slot_repeated"]
    assert done.counts["respond:generated"] == want["generated"]
    # the candidate version: one row per kept carrier, the plan row's columns copied (TD-017.8)
    version = await candidate(client, driver, run["id"])
    # Review R2: the candidate keeps every plan row beside the carriers; its census counts the
    # carriers only, so the cells generation left short are flagged for a top-up (FR-017.17/.20).
    census = version["behaviour_census"]
    assert census["counted"] == "rows with carrier text"
    assert census["rows"] == want["generated"] and census["planned_rows"] == 120
    assert census["cells_short"] > 0 and "cell_short" in [f["code"] for f in census["flags"]]
    assert census["templated"]["rows"] == 0, "the unfilled templated rows hold no text yet"
    assert any(w["code"] == "behaviour_census_flags" for w in version["warnings"])
    assert plan["behaviour_census"]["counted"] == "plan rows"
    by_key = {r["_dw_row_key"]: r for r in rows}
    generated = [r for r in version_rows(data_dir, version) if r["_dw_origin"] == "generated"]
    assert len(generated) == want["generated"]
    for row in generated:
        plan_row = by_key[row["_dw_parent_keys"][-1]]
        assert row["text"].count(run["slot_token"]) == 1 and row["slot"] == run["slot_token"]
        assert (row["generator"], row["generator_revision"]) == (GEN_MODEL, REVISION)
        for column in (
            "genre",
            "placement",
            "framing",
            "kind",
            "form",
            "length_band",
            "carrier_id",
        ):
            assert row[column] == plan_row[column], column
        assert row["behaviour_key"] == "fixture-alpha" and row["definition_version"] == "1"
        assert row["planned_element_valid"] == plan_row["planned_element_valid"]


async def test_instructed_carriers_write_one_whole_text_per_plan_row(
    client: httpx.AsyncClient, gen: Gen, driver: RealDriver, data_dir: Path
) -> None:
    """FR-017.15 (fixture-beta): one request per plan row, rendered from its planned cells; the
    rows record placement ``whole`` and ``planned_*`` equal to the plan."""
    plan = await plan_version(client, driver, data_dir, "fixture-beta", 60)
    rows = model_route(plan_rows(data_dir, plan))
    assert len(rows) == 60
    mapping = await template_map(
        client, plan, {"system": INSTRUCTED_SYSTEM, "prompt": INSTRUCTED_PROMPT}, "beta-inst"
    )
    run = await started(client, run_body(plan, mapping, "carrier_instructed"))
    assert run["slot_token"] is None
    assert gen.run_until_done(run["id"]).state == "completed"
    chats = gen.chats()
    by_carrier = {r["carrier_id"]: r for r in rows}
    assert sorted(carrier_of(c) for c in chats) == sorted(by_carrier)
    for chat in chats:
        plan_row = by_carrier[carrier_of(chat)]
        system, user = chat.body["messages"]
        assert system["content"] == f"You write {plan_row['genre']} texts."
        assert f"complied={plan_row['planned_complied']}" in user["content"]
        assert f"warned={plan_row['planned_warned']}" in user["content"]
    version = await candidate(client, driver, run["id"])
    generated = [r for r in version_rows(data_dir, version) if r["_dw_origin"] == "generated"]
    assert len(generated) == 60
    census = version["behaviour_census"]
    assert (census["rows"], census["cells_short"]) == (60, 0), "every carrier kept: no shortfall"
    for row in generated:
        plan_row = by_carrier[row["carrier_id"]]
        assert row["placement"] == "whole" and row["slot"] is None
        assert row["planned_complied"] == plan_row["planned_complied"]
        assert row["planned_warned"] == plan_row["planned_warned"]
        assert row["text"] and row["generator"] == GEN_MODEL


async def test_a_carrier_whose_plan_row_is_not_in_the_input_is_refused(
    client: httpx.AsyncClient, gen: Gen, driver: RealDriver, data_dir: Path
) -> None:
    """Review R2 (TD-017.8): ``dw_generated_rows`` copies each carrier's columns from its plan
    row in the build's input. An input missing one carrier's plan row (a child that dropped it)
    is refused ``seed_row_unknown``, never a carrier written with empty plan columns."""
    import pyarrow as pa

    from src.operators.errors import OperatorError
    from src.operators.native.generation import GeneratedRows
    from tests.integration.minimal_pairs.test_minimal_pair_operators import context

    plan = await plan_version(client, driver, data_dir, "fixture-beta", 12)
    mapping = await template_map(client, plan, {"prompt": INSTRUCTED_PROMPT}, "beta-unknown")
    run = await started(client, run_body(plan, mapping, "carrier_instructed"))
    assert gen.run_until_done(run["id"]).state == "completed"
    train = next(s for s in plan["splits"] if s["name"] == "train")
    table = pq.read_table(data_dir / train["path"])
    params = {"generation_run_id": run["id"], "target_type": "detector"}
    bindings = [{"kind": "generation_run", "id": run["id"]}]
    whole = GeneratedRows().run(table, params, context(GeneratedRows.manifest, table, bindings))
    assert whole.added is not None and whole.added.num_rows == 12
    short = table.slice(1)
    assert isinstance(short, pa.Table)
    with pytest.raises(OperatorError) as refused:
        GeneratedRows().run(short, params, context(GeneratedRows.manifest, short, bindings))
    assert refused.value.code == "seed_row_unknown"


async def test_two_generators_are_recorded_row_by_row(
    client: httpx.AsyncClient, gen: Gen, driver: RealDriver, data_dir: Path
) -> None:
    """FR-017.18: one run per generator; a version built from both records each row's own."""
    plan = await plan_version(client, driver, data_dir, "fixture-beta", 20)
    mapping = await template_map(
        client, plan, {"system": INSTRUCTED_SYSTEM, "prompt": INSTRUCTED_PROMPT}, "beta-two"
    )
    first = await started(client, run_body(plan, mapping, "carrier_instructed"))
    assert gen.run_until_done(first["id"]).state == "completed"
    gen.fake.resident = {**(gen.fake.resident or {}), "name": "gen-model-b", "revision": "b" * 40}
    second = await started(
        client, run_body(plan, mapping, "carrier_instructed", generator_model_id="gen-model-b")
    )
    assert gen.run_until_done(second["id"]).state == "completed"
    assert {c.body["model"] for c in gen.chats()} == {GEN_MODEL, "gen-model-b"}
    steps = [
        {
            "operator": "dw_generated_rows",
            "version": "1",
            "params": {"generation_run_id": run["id"], "target_type": "detector"},
        }
        for run in (first, second)
    ]
    from tests.integration.test_version_build import request, setup

    ds, rev = await setup(
        client, {"format": "dw.recipe/v1", "steps": steps}, name="two-gens", target="detector"
    )
    response = await request(
        client,
        ds,
        rev,
        [{"kind": "version", "version_id": plan["id"]}],
        seed=1,
        bindings=[{"kind": "generation_run", "id": r["id"]} for r in (first, second)],
    )
    assert response.status_code == 202, response.text
    assert driver.run(response.json()["job_id"]) == "completed"
    with sync_session_factory()() as db:
        from src.models.version import VersionBuild

        built = db.get(VersionBuild, response.json()["job_id"])
        assert built is not None
        version_id = str(built.version_id)
    version = (await client.get(f"/api/v1/versions/{version_id}")).json()
    generated = [r for r in version_rows(data_dir, version) if r["_dw_origin"] == "generated"]
    by_run = {first["id"]: (GEN_MODEL, REVISION), second["id"]: ("gen-model-b", "b" * 40)}
    assert Counter(r["generation_run_id"] for r in generated) == {first["id"]: 20, second["id"]: 20}
    for row in generated:
        assert (row["generator"], row["generator_revision"]) == by_run[row["generation_run_id"]]


@pytest.mark.parametrize(
    ("key", "mode", "body"),
    [
        ("fixture-alpha", "carrier_slot", {"prompt": SLOT_PROMPT}),
        ("fixture-beta", "carrier_instructed", {"prompt": INSTRUCTED_PROMPT}),
    ],
)
async def test_a_resumed_run_sends_no_completed_row_twice(
    client: httpx.AsyncClient,
    gen: Gen,
    driver: RealDriver,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    mode: str,
    body: dict[str, Any],
) -> None:
    """FR-017.19: the existing 007 resume: committed chunks are kept, nothing is sent twice."""
    monkeypatch.setattr(get_settings(), "generation_chunk_size", 4)
    plan = await plan_version(client, driver, data_dir, key, 40)
    rows = model_route(plan_rows(data_dir, plan))
    mapping = await template_map(client, plan, body, f"{key}-resume")
    if mode == "carrier_slot":
        gen.fake.gen_answer = slot_answer

    def lose(_: dict[str, Any]) -> None:
        if gen.fake.chat_calls == 6:
            gen.fake.restart()
            with sync_session_factory()() as db:
                db.execute(update(ModelLease).values(state="lost", lost_reason="restart"))
                db.commit()

    gen.fake.on_chat = lose
    run = await started(client, run_body(plan, mapping, mode))
    stopped = gen.run_until_done(run["id"])
    assert stopped.state == "failed" and stopped.error["code"] == "LEASE_LOST", stopped.error
    sent_before = len(gen.chats())
    assert 0 < sent_before < len(rows)
    gen.fake.on_chat = None
    resumed = await client.post(f"{RUNS}/{run['id']}/resume")
    assert resumed.status_code == 200, resumed.text
    gen.run_job(gen.job_for(run["id"]))
    assert gen.run(run["id"]).state == "completed"
    sent = Counter(carrier_of(c) for c in gen.chats())
    assert sorted(sent) == sorted(r["carrier_id"] for r in rows)
    assert set(sent.values()) == {1}, "no completed row was sent again"


async def test_a_resumed_run_with_requests_in_flight_resends_no_recorded_row(
    client: httpx.AsyncClient,
    gen: Gen,
    driver: RealDriver,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review R2 (FR-017.19 with TD-017.12): a lease lost while 3 requests are in flight. A row
    whose record was committed is never sent again; only answers in flight at the stop (never
    recorded) may be, at most ``width - 1`` of them; the resumed run completes every row."""
    width = 3
    monkeypatch.setattr(get_settings(), "generation_chunk_size", 4)
    monkeypatch.setattr(get_settings(), "generation_requests_in_flight", width)
    plan = await plan_version(client, driver, data_dir, "fixture-beta", 40)
    rows = model_route(plan_rows(data_dir, plan))
    mapping = await template_map(client, plan, {"prompt": INSTRUCTED_PROMPT}, "beta-resume-3")

    def lose(_: dict[str, Any]) -> None:
        if gen.fake.chat_calls == 10:
            gen.fake.restart()
            with sync_session_factory()() as db:
                db.execute(update(ModelLease).values(state="lost", lost_reason="restart"))
                db.commit()

    gen.fake.on_chat = lose
    run = await started(client, run_body(plan, mapping, "carrier_instructed"))
    assert run["requests_in_flight"] == width
    stopped = gen.run_until_done(run["id"])
    assert stopped.state == "failed" and stopped.error["code"] == "LEASE_LOST", stopped.error
    with sync_session_factory()() as db:
        recorded = set(
            db.execute(
                select(record_store.GenerationRecord.seed_row_key).where(
                    record_store.GenerationRecord.run_id == run["id"]
                )
            ).scalars()
        )
    assert recorded, "a chunk was committed before the stop"
    by_key = {r["_dw_row_key"]: r["carrier_id"] for r in rows}
    recorded_carriers = {by_key[k] for k in recorded}
    gen.fake.on_chat = None
    resumed = await client.post(f"{RUNS}/{run['id']}/resume")
    assert resumed.status_code == 200, resumed.text
    gen.run_job(gen.job_for(run["id"]))
    assert gen.run(run["id"]).state == "completed"
    sent = Counter(carrier_of(c) for c in gen.chats())
    assert sorted(sent) == sorted(r["carrier_id"] for r in rows)
    again = {c for c, k in sent.items() if k > 1}
    assert not (again & recorded_carriers), "a recorded row was sent again"
    assert len(again) <= width - 1, again


async def test_the_plan_estimate_is_not_measured_until_a_run_measures_it(
    client: httpx.AsyncClient, gen: Gen, driver: RealDriver, data_dir: Path
) -> None:
    """FR-017.19: "not measured" before any run of this behaviour and generator, never zero;
    after a run with a known rate, the estimate is rows / rate."""
    plan = await plan_version(client, driver, data_dir, "fixture-beta", 30)
    mapping = await template_map(client, plan, {"prompt": INSTRUCTED_PROMPT}, "beta-est")
    body = run_body(plan, mapping, "carrier_instructed")
    before = (await client.post(f"{RUNS}/plan", json=body)).json()
    assert before["estimate"]["rows"] == 30 and before["estimate"]["rows_per_run_hour"] is None
    assert before["estimate"]["run_hours"] is None
    assert before["estimate"]["basis"].startswith("not measured")
    run = await started(client, body)
    done = gen.run_until_done(run["id"])
    generated = int(done.counts["respond:generated"])
    with sync_session_factory()() as db:
        (job_id,) = db.execute(
            select(GenerationRunJob.job_id).where(GenerationRunJob.run_id == run["id"])
        ).scalars()
        job = db.get(Job, job_id)
        assert job is not None and job.started_at is not None
        # a rate of 100 carriers per working hour
        job.completed_at = job.started_at + timedelta(seconds=generated * 36)
        db.commit()
    after = (await client.post(f"{RUNS}/plan", json=body)).json()
    assert after["estimate"]["rows_per_run_hour"] == pytest.approx(100.0)
    assert after["estimate"]["run_hours"] == pytest.approx(30 / 100.0)
    other = (
        await client.post(f"{RUNS}/plan", json={**body, "behaviour_key": "another-key"})
    ).json()
    assert other["estimate"]["rows_per_run_hour"] is None, "per behaviour"
    # Review R2: a run that did not complete is not a measurement (ten times slower here)
    slow = await started(client, body)
    assert gen.run_until_done(slow["id"]).state == "completed"
    with sync_session_factory()() as db:
        (slow_job,) = db.execute(
            select(GenerationRunJob.job_id).where(GenerationRunJob.run_id == slow["id"])
        ).scalars()
        job = db.get(Job, slow_job)
        assert job is not None and job.started_at is not None
        job.completed_at = job.started_at + timedelta(seconds=generated * 360)
        db.execute(
            update(GenerationRun).where(GenerationRun.id == slow["id"]).values(state="failed")
        )
        db.commit()
    still = (await client.post(f"{RUNS}/plan", json=body)).json()
    assert still["estimate"]["rows_per_run_hour"] == pytest.approx(100.0), still["estimate"]


async def test_a_top_up_generates_only_the_named_cells(
    client: httpx.AsyncClient, gen: Gen, driver: RealDriver, data_dir: Path
) -> None:
    """FR-017.20: rows of two named cells only (payloads asserted); the version built from the
    top-up names the earlier version and the cells in its lineage."""
    plan = await plan_version(client, driver, data_dir, "fixture-beta", 80)
    rows = model_route(plan_rows(data_dir, plan))
    dims = ("genre", "placement", "framing", "length_band")
    cells = sorted({tuple(r[d] for d in dims) for r in rows})[:2]
    wanted = [r for r in rows if tuple(r[d] for d in dims) in cells]
    mapping = await template_map(client, plan, {"prompt": INSTRUCTED_PROMPT}, "beta-top")
    responds_to = {
        "version_id": plan["id"],
        "cells": [dict(zip(dims, c, strict=True)) for c in cells],
    }
    run = await started(
        client, run_body(plan, mapping, "carrier_instructed", responds_to=responds_to)
    )
    assert run["responds_to"] == responds_to and run["sample_size"] == len(wanted)
    assert gen.run_until_done(run["id"]).state == "completed"
    assert sorted(carrier_of(c) for c in gen.chats()) == sorted(r["carrier_id"] for r in wanted)
    version = await candidate(client, driver, run["id"])
    (step,) = [s for s in version["step_reports"] if s["operator"] == "dw_generated_rows"]
    assert step["reports"][0]["responds_to"] == responds_to
    undeclared = await client.post(
        f"{RUNS}/plan",
        json=run_body(
            plan,
            mapping,
            "carrier_instructed",
            responds_to={**responds_to, "cells": [{**responds_to["cells"][0], "genre": "nope"}]},
        ),
    )
    assert undeclared.status_code == 422, undeclared.text
    assert undeclared.json()["error"]["code"] == "vocabulary_value_undeclared"


async def test_the_requests_in_flight_setting_is_recorded_at_start(
    client: httpx.AsyncClient,
    gen: Gen,
    driver: RealDriver,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TD-017.12: GENERATION_REQUESTS_IN_FLIGHT as it was when the run started."""
    plan = await plan_version(client, driver, data_dir, "fixture-beta", 10)
    mapping = await template_map(client, plan, {"prompt": INSTRUCTED_PROMPT}, "beta-flight")
    first = await started(client, run_body(plan, mapping, "carrier_instructed"))
    monkeypatch.setattr(get_settings(), "generation_requests_in_flight", 3)
    second = await started(client, run_body(plan, mapping, "carrier_instructed"))
    assert (first["requests_in_flight"], second["requests_in_flight"]) == (1, 3)
    again = (await client.get(f"{RUNS}/{first['id']}")).json()
    assert again["requests_in_flight"] == 1


class _Overlap:
    """A fake generator that holds each answer briefly and counts how many it holds at once."""

    def __init__(self) -> None:
        import threading

        self.lock = threading.Lock()
        self.now = 0
        self.most = 0

    def __call__(self, messages: list[dict[str, Any]], body: dict[str, Any]) -> str:
        import time

        with self.lock:
            self.now += 1
            self.most = max(self.most, self.now)
        time.sleep(0.05)
        with self.lock:
            self.now -= 1
        return f"Whole text for {CARRIER.search(json.dumps(messages)).group(1)}."  # type: ignore[union-attr]


async def test_the_requests_in_flight_setting_is_honoured_by_the_worker(
    client: httpx.AsyncClient,
    gen: Gen,
    driver: RealDriver,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review R2 (TD-017.12): the recorded setting is what the worker keeps in flight, not a
    label. With 4, the fake generator holds 4 requests at once; with 1, never 2. Each plan row is
    sent once either way, and the two runs write the same records in the same order."""
    monkeypatch.setattr(get_settings(), "generation_chunk_size", 10)
    plan = await plan_version(client, driver, data_dir, "fixture-beta", 24)
    rows = model_route(plan_rows(data_dir, plan))
    mapping = await template_map(client, plan, {"prompt": INSTRUCTED_PROMPT}, "beta-overlap")
    written = {}
    for width in (4, 1):
        overlap = _Overlap()
        gen.fake.gen_answer = overlap
        monkeypatch.setattr(get_settings(), "generation_requests_in_flight", width)
        before = len(gen.chats())
        run = await started(client, run_body(plan, mapping, "carrier_instructed"))
        assert run["requests_in_flight"] == width
        done = gen.run_until_done(run["id"])
        assert done.state == "completed", done.error
        chats = gen.chats()[before:]
        assert sorted(carrier_of(c) for c in chats) == sorted(r["carrier_id"] for r in rows)
        assert overlap.most == width, (width, overlap.most)
        with sync_session_factory()() as db:
            records = db.execute(
                select(record_store.GenerationRecord)
                .where(record_store.GenerationRecord.run_id == run["id"])
                .order_by(record_store.GenerationRecord.record_index)
            ).scalars()
            written[width] = [(r.record_index, r.seed_row_key, r.outcome) for r in records]
    assert written[4] == written[1] and len(written[1]) == len(rows)


async def test_only_a_native_carrier_run_records_more_than_one_in_flight(
    client: httpx.AsyncClient,
    gen: Gen,
    driver: RealDriver,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review R2: the recorded value is what the worker will use. With the setting at 4, a
    standard run and a carrier run on the relay path (one in-process stage per call) record 1."""
    from tests.support.generation_fixtures import (
        make_version,
        respond_template,
        texts,
        version_table,
    )
    from tests.support.generation_fixtures import run_body as standard_body

    monkeypatch.setattr(get_settings(), "generation_requests_in_flight", 4)
    version = make_version(version_table(texts(4)))
    standard = await started(client, standard_body(version, await respond_template(client)))
    assert standard["requests_in_flight"] == 1
    plan = await plan_version(client, driver, data_dir, "fixture-beta", 10)
    mapping = await template_map(client, plan, {"prompt": INSTRUCTED_PROMPT}, "beta-relay")
    native = await started(client, run_body(plan, mapping, "carrier_instructed"))
    assert native["requests_in_flight"] == 4
    monkeypatch.setattr(get_settings(), "generation_engine_path", "relay")
    relay = await started(client, run_body(plan, mapping, "carrier_instructed"))
    assert relay["requests_in_flight"] == 1


async def test_refusals_name_what_to_fix(
    client: httpx.AsyncClient, gen: Gen, driver: RealDriver, data_dir: Path
) -> None:
    plan = await plan_version(client, driver, data_dir, "fixture-alpha", 30)
    mapping = await template_map(client, plan, {"prompt": SLOT_PROMPT}, "alpha-ref")
    genres = plan["behaviour_configuration"]["vocabulary"]["genres"]
    short = {"column": "genre", "templates": {g: mapping["templates"][g] for g in genres[1:]}}
    unmapped = await client.post(f"{RUNS}/plan", json=run_body(plan, short, "carrier_slot"))
    assert unmapped.status_code == 422, unmapped.text
    error = unmapped.json()["error"]
    assert error["code"] == "template_unmapped_value"
    assert error["details"]["column"] == "genre" and error["details"]["value"] == genres[0]
    sized = await client.post(
        f"{RUNS}/plan", json=run_body(plan, mapping, "carrier_slot", sample_size=1)
    )
    assert sized.json()["error"]["code"] == "carrier_sample_size", sized.text
    other = await client.post(
        f"{RUNS}/plan", json=run_body(plan, mapping, "carrier_slot", generator_model_id="absent")
    )
    assert other.status_code == 409 and other.json()["error"]["code"] == "MODEL_NOT_LOADED"
    assert other.json()["error"]["details"]["needed"] == "absent"
    from tests.support.generation_fixtures import make_version as plain_version
    from tests.support.generation_fixtures import set_role, texts, version_table

    not_a_plan = plain_version(version_table(texts(3)), target_type="detector")
    refused = await client.post(
        f"{RUNS}/plan",
        json={**run_body(plan, mapping, "carrier_slot"), "input_version_id": not_a_plan},
    )
    assert refused.json()["error"]["code"] == "not_a_carrier_plan", refused.text
    from tests.support.fake_openai import FakeOpenAI

    with FakeOpenAI() as upstream:
        set_role("generation", upstream.base_url, "m1")
        unserved = await client.post(f"{RUNS}/plan", json=run_body(plan, mapping, "carrier_slot"))
    assert unserved.status_code == 409, unserved.text
    assert unserved.json()["error"]["code"] == "generator_not_served"
    assert unserved.json()["error"]["details"]["next"]["tool"] == "dataworks_fetch_models"
    # FR-017.57: each refusal names a tool of the LIVE MCP registry
    from tests.integration.rules.conftest import live_tools

    tools = await live_tools()
    for answer in (unmapped, sized, refused, unserved):
        assert answer.json()["error"]["details"]["next"]["tool"] in tools, answer.text


async def test_the_template_check_refuses_at_save_and_at_plan(
    client: httpx.AsyncClient, gen: Gen, driver: RealDriver, data_dir: Path
) -> None:
    """FR-017.16 wiring: the check runs at template save (named rule sets, phrases) and at plan
    over every mapped template (the run's pinned rule sets and the plan's phrase list)."""
    from tests.integration.rules.conftest import fixture_document

    saved = await client.post(
        "/api/v1/rule-sets", json={"document": fixture_document("fixture-alpha")}
    )
    assert saved.status_code == 201, saved.text
    pin = {k: saved.json()[k] for k in ("name", "version", "sha256")}
    showing = {"prompt": SLOT_PROMPT + " Like: Vexilloquant: 4111 1111 1111 1111."}
    refused = await client.post(
        TEMPLATES,
        json={"name": "alpha-shows", "kind": "respond", "body": showing, "check_rule_sets": [pin]},
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["code"] == "slot_template_has_element"
    assert refused.json()["error"]["details"]["rule_set"] == pin["name"]
    phrase = await client.post(
        TEMPLATES,
        json={
            "name": "alpha-phrase",
            "kind": "respond",
            "body": {"prompt": SLOT_PROMPT + " For Reference."},
            "check_phrases": ["for reference"],
        },
    )
    assert phrase.json()["error"]["code"] == "slot_template_has_element", phrase.text
    plan = await plan_version(client, driver, data_dir, "fixture-alpha", 30)
    genres = plan["behaviour_configuration"]["vocabulary"]["genres"]
    clean = await template_map(client, plan, {"prompt": SLOT_PROMPT}, "alpha-clean")
    accepted = await client.post(
        f"{RUNS}/plan", json=run_body(plan, clean, "carrier_slot", rule_sets=[pin])
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["carrier"]["checked_with"]["rule_sets"] == [pin]
    shows = await template(client, "alpha-unchecked", showing)
    mixed = {"column": "genre", "templates": {**clean["templates"], genres[0]: shows}}
    at_plan = await client.post(
        f"{RUNS}/plan", json=run_body(plan, mixed, "carrier_slot", rule_sets=[pin])
    )
    assert at_plan.status_code == 422, at_plan.text
    assert at_plan.json()["error"]["details"]["template"] == "alpha-unchecked@1"
    # the plan's own phrase list ("for reference") is checked without being named
    phrased = await template(client, "alpha-phrased", {"prompt": SLOT_PROMPT + " for reference"})
    by_phrase = await client.post(
        f"{RUNS}/plan",
        json=run_body(
            plan, {"column": "genre", "templates": {**clean["templates"], genres[1]: phrased}},
            "carrier_slot",
        ),
    )  # fmt: skip
    assert by_phrase.json()["error"]["details"]["phrase"] == "for reference", by_phrase.text


async def test_both_modes_reach_through_rest_and_mcp(
    client: httpx.AsyncClient, gen: Gen, driver: RealDriver, data_dir: Path
) -> None:
    """5.1 / 5.11 reach: each carrier mode is accepted by the REST route and by the MCP tools
    (``dataworks_plan_generation``, ``dataworks_start_generation``) over the same backend."""
    plan = await plan_version(client, driver, data_dir, "fixture-alpha", 30)
    mapping = await template_map(client, plan, {"prompt": SLOT_PROMPT}, "alpha-reach")
    for mode in ("carrier_slot", "carrier_instructed"):
        rest = await client.post(f"{RUNS}/plan", json=run_body(plan, mapping, mode))
        assert rest.status_code == 200 and rest.json()["mode"] == mode, rest.text
    mcp, mcp_client = live_mcp()
    args = {
        "input_version_id": plan["id"],
        "prompt_column": "text",
        "seed_splits": ["train"],
        "target_type": "detector",
        "generator_model_id": GEN_MODEL,
        "template_map": mapping,
        "behaviour_key": "fixture-alpha",
    }
    try:
        planned = await call(
            mcp, "dataworks_plan_generation", {**args, "mode": "carrier_instructed"}
        )
        assert planned["mode"] == "carrier_instructed", planned
        assert planned["carrier"]["generator_model_id"] == GEN_MODEL
        run = await call(mcp, "dataworks_start_generation", {**args, "mode": "carrier_slot"})
    finally:
        await mcp_client.close()
    assert run["mode"] == "carrier_slot" and run["started_by_origin"] == "agent", run
    assert run["behaviour_key"] == "fixture-alpha"
