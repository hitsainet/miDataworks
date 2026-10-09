"""Steered pairs against the fake miLLM (007 FTASKS 10.2 – 10.8, 6.16, 6.12).

The fake has a GLOBALLY ACTIVE profile, so "unsteered" is never trivially true (FPRD 007 12.6):
an unsteered side must send the explicit empty set or miLLM reports the active profile.
"""

from __future__ import annotations

from typing import Any

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from sqlalchemy import select

from src.core.database import sync_session_factory
from src.models.generation import GenerationPair, GenerationRecord, SteeringSnapshot
from src.services.generation import record_store, steering
from tests.support.generation_fixtures import (
    SAE,
    Gen,
    make_version,
    respond_template,
    run_body,
    version_table,
)

RUNS = "/api/v1/generation-runs"
DPO_ROLES = {"prompt": "content", "chosen": "content", "rejected": "content"}
PROFILE = {
    "id": "prof-1",
    "name": "humor",
    "description": None,
    "model_id": "gen-model-7b",
    "sae_id": SAE,
    "layer": 11,
    "steering": {"4127": 6.0},
    "is_active": False,
    "source_kind": "manual",
    "intensity": 1.0,
    "sensing_enabled": False,
    "created_at": "2026-10-01T00:00:00Z",
    "updated_at": "2026-10-01T00:00:00Z",
}
ACTIVE = {
    **PROFILE,
    "id": "prof-active",
    "name": "always-on",
    "steering": {"9": 3.0},
    "is_active": True,
}


def dpo_version(texts: list[str]) -> str:
    table = version_table(texts)
    table = table.append_column("chosen", pa.array([None] * table.num_rows, pa.string()))
    table = table.append_column("rejected", pa.array([None] * table.num_rows, pa.string()))
    return make_version(table, target_type="dpo", roles={**DPO_ROLES, "completion": "metadata"})


def steered_body(version: str, template: str, **kw: Any) -> dict[str, Any]:
    return run_body(
        version,
        template,
        mode="steered_pairs",
        target_type="dpo",
        setting_a={"kind": "inline", "sae_id": SAE, "features": [{"index": 4127, "strength": 0.0}]},
        setting_b={"kind": "profile", "profile_name": "humor"},
        chosen_side="b",
        **kw,
    )


@pytest.fixture
def profiles(gen: Gen) -> Gen:
    gen.fake.profiles = {"prof-1": dict(PROFILE), "prof-active": dict(ACTIVE)}
    gen.fake.active_profile_id = "prof-active"
    return gen


def records(run_id: str) -> list[GenerationRecord]:
    with sync_session_factory()() as db:
        rows = list(
            db.execute(
                select(GenerationRecord)
                .where(GenerationRecord.run_id == run_id)
                .order_by(GenerationRecord.record_index)
            ).scalars()
        )
        for r in rows:
            db.expunge(r)
        return rows


async def test_compare_reports_the_one_differing_index(
    client: httpx.AsyncClient, profiles: Gen
) -> None:
    body = {
        "setting_a": {
            "kind": "inline",
            "sae_id": SAE,
            "features": [{"index": 4127, "strength": 0.0}],
        },
        "setting_b": {"kind": "profile", "profile_name": "humor"},
    }
    response = await client.post("/api/v1/steering-settings/compare", json=body)
    assert response.status_code == 200, response.text
    out = response.json()
    assert out["one_axis"] is True and out["differing_index"] == 4127
    assert out["message"] == "Differs on feature 4127 only: 0 vs 6."
    b = out["snapshots"][1]
    assert b["set_hash"] == steering.applied_set_hash(SAE, [(4127, 6.0)]) and b["layer"] == 11


async def test_pairs_form_only_on_matching_headers(
    client: httpx.AsyncClient, profiles: Gen
) -> None:
    def mode(body: dict[str, Any]) -> str:
        # side B of the second prompt is reported with a wrong hash
        return (
            "wrong_hash"
            if "profile" in body and "two" in body["messages"][-1]["content"]
            else "real"
        )

    profiles.fake.header_mode = mode
    version = dpo_version(["prompt one", "prompt two", "prompt three"])
    template = await respond_template(client)
    response = await client.post(RUNS, json=steered_body(version, template, sample_size=3))
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    with sync_session_factory()() as db:
        snaps = {
            s.side: s
            for s in db.execute(
                select(SteeringSnapshot).where(SteeringSnapshot.run_id == run_id)
            ).scalars()
        }
    assert set(snaps) == {"generator", "a", "b"}
    assert snaps["b"].profile_updated_at == "2026-10-01T00:00:00Z"
    run = profiles.run_until_done(run_id)
    assert run.state == "completed", run.error
    rows = records(run_id)
    assert len(rows) == 6
    sides = {(r.prompt_row_key, r.side): r for r in rows}
    calls = profiles.chats()
    a_calls = [c for c in calls if "steering" in c.body]
    b_calls = [c for c in calls if "profile" in c.body]
    assert len(a_calls) == len(b_calls) == 3
    # every strength of side A is 0: it is sent as the explicit unsteered set — miLLM refuses an
    # sae_id beside an empty feature list (FR-28.2.2) — and the one-axis rule still saw 4127 at 0
    assert all(c.body["steering"] == {"features": []} for c in a_calls)
    for a, b in zip(a_calls, b_calls, strict=True):
        assert a.body["seed"] == b.body["seed"] and a.body["messages"] == b.body["messages"]
    with sync_session_factory()() as db:
        pairs = list(
            db.execute(select(GenerationPair).where(GenerationPair.run_id == run_id)).scalars()
        )
    assert len(pairs) == 2 and {p.chosen_side for p in pairs} == {"b"}
    discarded = [r for r in rows if r.outcome == "discarded"]
    assert {(r.side, r.reason_code) for r in discarded} == {
        ("b", "steering_mismatch"),
        ("a", "pair_partner_discarded"),
    }
    # side a sent a zero strength: miLLM reports `none` and the expected applied set is empty
    assert all(
        r.reported_steering == "none" for r in rows if r.side == "a" and r.outcome != "discarded"
    )
    del sides


async def test_an_active_profile_voids_an_unsteered_side_without_the_explicit_body(
    client: httpx.AsyncClient, profiles: Gen, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before 028 the unsteered body is not sent; the active profile then steers the request and
    the record says so (never 'unsteered')."""
    from src.core.config import get_settings

    version = make_version(version_table(["one"]))
    template = await respond_template(client)
    response = await client.post(RUNS, json=run_body(version, template, sample_size=1))
    run_id = response.json()["id"]
    monkeypatch.setattr(profiles.fake, "header_mode", lambda body: "real")

    def no_explicit(body: dict[str, Any]) -> None:
        body.pop("steering", None)  # what a server that ignores the field would see

    profiles.fake.on_chat = no_explicit
    run = profiles.run_until_done(run_id)
    (row,) = records(run_id)
    assert row.outcome == "discarded" and "kind" in row.check_reasons
    assert row.reported_steering and row.reported_steering.startswith(
        'profile;name="always-on";source=active'
    )
    del get_settings, run


async def test_a_profile_edited_between_chunks_stops_the_run_and_resume_is_refused(
    client: httpx.AsyncClient, profiles: Gen, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.core.config import get_settings

    monkeypatch.setattr(get_settings(), "generation_chunk_size", 1)
    version = dpo_version(["p one", "p two", "p three"])
    template = await respond_template(client)
    run_id = (await client.post(RUNS, json=steered_body(version, template, sample_size=3))).json()[
        "id"
    ]

    def edit(body: dict[str, Any]) -> None:
        if profiles.fake.chat_calls == 2:
            profiles.fake.profiles["prof-1"]["updated_at"] = "2026-10-07T14:02:00Z"

    profiles.fake.on_chat = edit
    run = profiles.run_until_done(run_id)
    assert run.state == "failed" and run.failure_reason == "profile_changed"
    assert run.error["code"] == "PROFILE_CHANGED" and "2026-10-07T14:02:00Z" in run.error["message"]
    assert len(records(run_id)) == 2, "the first chunk (one prompt, two sides) is kept"
    resume = await client.post(f"{RUNS}/{run_id}/resume")
    assert resume.status_code == 409 and resume.json()["error"]["code"] == "RUN_NOT_RESUMABLE"


async def test_cancel_then_resume_repeats_and_misses_nothing(
    client: httpx.AsyncClient, gen: Gen, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.core import cancellation
    from src.core.config import get_settings

    monkeypatch.setattr(get_settings(), "generation_chunk_size", 2)
    monkeypatch.setattr(cancellation, "DEFAULT_POLL_INTERVAL_S", 0.0)
    version = make_version(version_table([f"q {i}" for i in range(7)]))
    template = await respond_template(client)
    run_id = (await client.post(RUNS, json=run_body(version, template, sample_size=7))).json()["id"]
    job_id = gen.job_for(run_id)

    def cancel_at(position: int) -> None:
        if position == 3:
            from sqlalchemy import update

            from src.core.clock import utc_now
            from src.models.job import Job

            with sync_session_factory()() as db:
                db.execute(
                    update(Job)
                    .where(Job.id == job_id)
                    .values(status="cancelling", cancel_requested_at=utc_now())
                )
                db.commit()

    gen.deps_overrides["on_unit"] = cancel_at
    gen.run_job(job_id)
    run = gen.run(run_id)
    assert run.state == "cancelled"
    first = records(run_id)
    # the request lands during unit 3; the next record boundary stops before unit 4
    assert [r.seed_position for r in first] == [0, 1, 2, 3], "every finished unit is kept"
    gen.deps_overrides.clear()
    assert (await client.post(f"{RUNS}/{run_id}/resume")).status_code == 200
    gen.run_job(gen.job_for(run_id))
    rows = records(run_id)
    assert gen.run(run_id).state == "completed"
    assert [r.record_index for r in rows] == list(range(7))
    prompts = [c.body["messages"][-1]["content"] for c in gen.chats()]
    assert len(prompts) == len(set(prompts)) == 7, "no prompt was generated twice"


async def test_a_held_out_row_in_the_seed_table_is_refused_by_the_worker(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    version = make_version(version_table(["q 1", "q 2"]))
    template = await respond_template(client)
    run_id = (await client.post(RUNS, json=run_body(version, template, sample_size=2))).json()["id"]
    # the seed table appears (or is tampered with) after the plan: one held-out row in it
    table = pa.table(
        {
            "position": pa.array([0, 1], pa.int32()),
            "row_key": ["a" * 64, "b" * 64],
            "split": ["train", "test"],
            "origin": ["source", "source"],
            "prompt": ["q 1", "held-out question one"],
            "values": ["{}", "{}"],
        }
    )
    path = record_store.seeds_path(run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)
    run = gen.run_until_done(run_id)
    assert run.state == "failed" and run.error["code"] == "HELD_OUT_SEED"
    assert gen.chats() == [] and records(run_id) == []
