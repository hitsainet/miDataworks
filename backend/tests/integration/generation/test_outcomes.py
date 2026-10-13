"""Response outcomes, pinning and the headers on every call (007 FTASKS 6.13 – 6.15, 6.17, 10.7).

The fake miLLM answers each request with the steering its hooks ran (``fake_millm._applied``) or a
scripted defect chosen by the prompt text, so one run exercises every reason code. No discarded
response becomes a ``generated`` record, and every reason is counted.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy import select, text, update

from src.core.database import sync_session_factory
from src.models.generation import GenerationRecord
from src.models.model_lease import ModelLease
from tests.support.generation_fixtures import (
    GEN_MODEL,
    SAE,
    Gen,
    make_version,
    respond_template,
    run_body,
    version_table,
)

RUNS = "/api/v1/generation-runs"
MODES = {
    "MISMATCH": "wrong_hash",
    "MISSING": "missing",
    "UNKNOWN": "unknown",
    "CHANGED": "changed",
    "EXTRA": "extra_item",
}


def scripted(body: dict[str, Any]) -> str:
    content = str(body["messages"][-1]["content"])
    for marker, mode in MODES.items():
        if marker in content:
            return mode
    return "real"


async def start(client: httpx.AsyncClient, body: dict[str, Any]) -> str:
    response = await client.post(RUNS, json=body)
    assert response.status_code == 202, response.text
    return str(response.json()["id"])


def records(run_id: str) -> list[GenerationRecord]:
    with sync_session_factory()() as db:
        rows = list(
            db.execute(
                select(GenerationRecord)
                .where(GenerationRecord.run_id == run_id)
                .order_by(GenerationRecord.record_index)
            ).scalars()
        )
        for row in rows:
            db.expunge(row)
        return rows


PROMPTS = [
    "plain one",
    "MISMATCH two",
    "MISSING three",
    "UNKNOWN four",
    "CHANGED five",
    "EXTRA six",
    "OVERFLOW seven",
    "plain eight",
]


@pytest.mark.parametrize("path", ["native", "relay"])
async def test_every_outcome_is_recorded_with_its_reason(
    client: httpx.AsyncClient, gen: Gen, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    from src.core.config import get_settings

    monkeypatch.setattr(get_settings(), "generation_engine_path", path)
    gen.fake.header_mode = scripted
    gen.fake.busy.extend([2])  # the first request waits Retry-After: 2, then succeeds
    version = make_version(version_table(PROMPTS))
    template = await respond_template(client)
    run_id = await start(client, run_body(version, template, sample_size=len(PROMPTS)))
    run = gen.run_until_done(run_id)
    assert run.state == "completed", run.error
    assert run.engine_path == path
    by_prompt = {}
    with sync_session_factory()() as db:
        from src.services.generation import record_store

        for rec in record_store.iter_committed(
            record_store.committed_chunk_paths(db, run_id, "respond")
        ):
            by_prompt[rec.prompt] = rec
    expected = {
        "plain one": ("generated", None, "match"),
        "plain eight": ("generated", None, "match"),
        "MISMATCH two": ("discarded", "steering_mismatch", "mismatch"),
        "MISSING three": ("discarded", "steering_unreported", "unreported"),
        "UNKNOWN four": ("discarded", "steering_unreported", "unreported"),
        "CHANGED five": ("discarded", "steering_mismatch", "mismatch"),
        "EXTRA six": ("discarded", "steering_mismatch", "mismatch"),
        "OVERFLOW seven": ("skipped", "context_overflow", "not_applicable"),
    }
    got = {p: (r.outcome, r.reason_code, r.steering_check) for p, r in by_prompt.items()}
    assert got == expected
    assert by_prompt["MISSING three"].reported_steering is None, "absent, never 'unsteered'"
    assert by_prompt["UNKNOWN four"].reported_steering == "unknown;reason=read_failed"
    assert "changed" in by_prompt["CHANGED five"].check_reasons
    if path == "native":  # the relay waits inside its own loop (its records carry waited_s)
        assert gen.waits[:1] == [2.0], "503 waited Retry-After and was not a failure"
    assert run.counts["respond:generated"] == 2
    assert run.counts["reason:steering_unreported"] == 2
    assert run.counts["reason:steering_mismatch"] == 3
    assert run.counts["reason:context_overflow"] == 1
    with sync_session_factory()() as db:
        bad = db.execute(
            text(
                "SELECT count(*) FROM dw_generation_records WHERE run_id = :r AND "
                "outcome = 'generated' AND steering_check NOT IN ('match', 'not_applicable')"
            ),
            {"r": run_id},
        ).scalar_one()
    assert bad == 0


async def test_native_and_relay_give_identical_outcomes(
    client: httpx.AsyncClient, gen: Gen, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.core.config import get_settings

    gen.fake.header_mode = scripted
    template = await respond_template(client)
    outcomes = []
    for path in ("native", "relay"):
        monkeypatch.setattr(get_settings(), "generation_engine_path", path)
        version = make_version(version_table(PROMPTS))
        run_id = await start(client, run_body(version, template, sample_size=len(PROMPTS)))
        assert gen.run_until_done(run_id).state == "completed"
        outcomes.append(
            [
                (
                    r.record_index,
                    r.outcome,
                    r.reason_code,
                    r.steering_check,
                    r.seed_sent,
                    r.seed_confirmed,
                )
                for r in records(run_id)
            ]
        )
    assert outcomes[0] == outcomes[1]


async def test_a_clamped_inline_strength_is_a_mismatch_on_every_response(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    """FR-007.23: a standard run with one steering setting goes through the same checks."""
    version = make_version(version_table(["one", "two"]))
    template = await respond_template(client)
    setting = {"kind": "inline", "sae_id": SAE, "features": [{"index": 5, "strength": 500.0}]}
    run_id = await start(
        client, run_body(version, template, sample_size=2, generator_setting=setting)
    )
    run = gen.run_until_done(run_id)
    rows = records(run_id)
    assert run.state == "completed" and rows
    assert {r.outcome for r in rows} == {"discarded"}
    assert all("clamped" in r.check_reasons for r in rows)
    assert all(r.requested_set_hash and r.requested_set_hash.startswith("sha256:") for r in rows)
    sent = [c.body["steering"] for c in gen.chats()]
    assert sent and all(
        s == {"features": [{"index": 5, "strength": 500.0}], "sae_id": SAE} for s in sent
    )


async def test_every_call_carries_strict_refuse_load_and_the_lease(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    version = make_version(version_table(["a q", "b q", "c q"]))
    template = await respond_template(client)
    run_id = await start(client, run_body(version, template, sample_size=3, n_responses=2))
    before = len(gen.chats())
    run = gen.run_until_done(run_id)
    calls = gen.chats()[before:]
    assert run.pinned is True
    assert len(calls) == 6, "3 prompts x 2 responses, one request each"
    for call in calls:
        assert call.headers["x-millm-strict"] == "true"
        assert call.headers["x-millm-load-policy"] == "refuse"
        assert call.headers.get("x-millm-lease", "").startswith("lease-")


async def test_a_different_resident_model_refuses_the_run(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    version = make_version(version_table(["a q"]))
    template = await respond_template(client)
    run_id = await start(client, run_body(version, template, sample_size=1))
    gen.fake.resident = {**(gen.fake.resident or {}), "name": "another-model"}
    run = gen.run_until_done(run_id)
    assert run.state == "failed" and run.error["code"] == "MODEL_NOT_LOADED"
    assert not gen.chats()


async def test_a_lost_lease_stops_at_the_chunk_boundary_and_is_resumable(
    client: httpx.AsyncClient, gen: Gen, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.core.config import get_settings

    monkeypatch.setattr(get_settings(), "generation_chunk_size", 2)
    version = make_version(version_table([f"q {i}" for i in range(6)]))
    template = await respond_template(client)
    run_id = await start(client, run_body(version, template, sample_size=6))

    def lose(body: dict[str, Any]) -> None:
        if gen.fake.chat_calls == 2:
            gen.fake.restart()  # a miLLM restart ends every lease (miLLM FR-29.1.9)
            with sync_session_factory()() as db:
                db.execute(update(ModelLease).values(state="lost", lost_reason="restart"))
                db.commit()

    gen.fake.on_chat = lose
    run = gen.run_until_done(run_id)
    assert run.state == "failed" and run.error["code"] == "LEASE_LOST"
    assert run.failure_reason is None, "a lost lease is resumable"
    assert len(records(run_id)) == 2, "the committed chunk survives"
    gen.fake.on_chat = None
    response = await client.post(f"{RUNS}/{run_id}/resume")
    assert response.status_code == 200, response.text
    outcome = gen.run_job(gen.job_for(run_id))
    run = gen.run(run_id)
    assert run.state == "completed", (outcome, run.error)
    assert run.state == "completed"
    assert [r.record_index for r in records(run_id)] == list(range(6))


async def test_a_response_naming_another_model_stops_the_run(
    client: httpx.AsyncClient, gen: Gen, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.core.config import get_settings

    monkeypatch.setattr(get_settings(), "generation_chunk_size", 2)
    gen.fake.swap_model_after = 3
    version = make_version(version_table([f"q {i}" for i in range(6)]))
    template = await respond_template(client)
    run_id = await start(client, run_body(version, template, sample_size=6))
    run = gen.run_until_done(run_id)
    assert run.state == "failed" and run.error["code"] == "MODEL_CHANGED"
    kept = records(run_id)
    assert kept and all(r.model_id == GEN_MODEL for r in kept)


async def test_a_non_millm_endpoint_is_unpinned_and_not_applicable(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    from tests.support.fake_openai import FakeOpenAI
    from tests.support.generation_fixtures import set_role

    with FakeOpenAI() as upstream:
        set_role("generation", upstream.base_url, "m1")
        set_role("judge", upstream.base_url, "judge-m")
        version = make_version(version_table(["a q", "b q"]))
        template = await respond_template(client)
        run_id = await start(client, run_body(version, template, sample_size=2))
        run = gen.run_until_done(run_id)
        assert run.state == "completed", run.error
        assert run.pinned is False and run.server_kind == "openai_compatible"
        rows = records(run_id)
        assert {r.steering_check for r in rows} == {"not_applicable"}
        assert {r.outcome for r in rows} == {"generated"}
        # control C34: no seed echo from this server, so the seed is "not confirmed", never True
        assert {r.seed_confirmed for r in rows} == {None}
        assert all("steering" not in r["body"] for r in upstream.requests), "no 028 body off miLLM"
