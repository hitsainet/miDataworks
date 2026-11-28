"""A GGUF generator (miServer's llama.cpp engine) end to end against the fake miServer (2026-11-16).

The live failure: a carrier run on a resident ``…-GGUF:Q4_K_M`` model failed at once with
ENDPOINT_FAILING after 20 ``ROW_ERROR``s, each ``400 field_not_honoured: 'steering' is not honoured
on /v1/chat/completions … with the llamacpp engine``. miDataworks sent the explicit unsteered set
``{"steering": {"features": []}}`` (FR-007.21) on every request, and miServer's request policy
refuses every steering field on llama.cpp, that one included (miServer ``api/request_policy.py``,
``OUTPUT_CHANGING["steering"]`` → ``_STEER_LC``, no ``NEUTRAL`` entry).

The fake refuses exactly as miServer does when ``engine == "llama.cpp"`` (``_llamacpp_refusal``),
and reports ``X-miLLM-Steering`` as miServer's llama.cpp path does (``steering_report.py``
``_describe``: ``none`` with no live SAE entry, ``unknown;reason=llamacpp_entries`` with one).

Pinned here:
- an unsteered GGUF run sends NO steering field, and records every row as unsteered: snapshot
  ``none`` with no set hash, ``steering_check`` ``match`` against the reported ``none``; never
  ``unreported``/``not_applicable``;
- the check is not weakened on GGUF: a reply reporting ``unknown`` is discarded as
  ``steering_unreported``;
- a steered setting on a GGUF generator is refused at plan time (``STEERING_UNSUPPORTED``), as is a
  structured-output template (``STRUCTURED_OUTPUT_UNSUPPORTED``); nothing is sent;
- generator identity and revision are recorded for the GGUF model.

The transformers path keeps the explicit empty set: ``test_standard_run.py`` and
``test_steered_run.py`` pin it.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from src.core.database import sync_session_factory
from src.models.generation import GenerationRecord, SteeringSnapshot
from tests.support.generation_fixtures import (
    Gen,
    make_version,
    respond_template,
    run_body,
    set_role,
    texts,
    version_table,
)

RUNS = "/api/v1/generation-runs"
GGUF_MODEL = "Example-Instruct-GGUF:Q4_K_M"
GGUF_REVISION = "0123456789abcdef0123456789abcdef01234567"


def make_gguf(gen: Gen) -> None:
    """Make the fake's resident model a GGUF row, as miServer lists one (``gguf_label`` set), served
    by llama.cpp; no SAE attaches to a GGUF model."""
    gen.fake.resident = {
        "id": 44,
        "name": GGUF_MODEL,
        "repo_id": "example/Example-Instruct-GGUF",
        "revision": GGUF_REVISION,
        "quantization": "Q4",
        "gguf_label": "Q4_K_M",
    }
    gen.fake.engine = "llama.cpp"
    gen.fake.attachments = []
    set_role("generation", gen.base_url, GGUF_MODEL)


def records_of(run_id: str) -> list[GenerationRecord]:
    with sync_session_factory()() as db:
        return list(
            db.execute(
                select(GenerationRecord)
                .where(GenerationRecord.run_id == run_id)
                .order_by(GenerationRecord.record_index)
            ).scalars()
        )


def snapshots_of(run_id: str) -> list[SteeringSnapshot]:
    with sync_session_factory()() as db:
        rows = list(
            db.execute(select(SteeringSnapshot).where(SteeringSnapshot.run_id == run_id)).scalars()
        )
        for row in rows:
            db.expunge(row)
        return rows


async def test_an_unsteered_gguf_run_sends_no_steering_field_and_records_unsteered(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    make_gguf(gen)
    version = make_version(version_table(texts(4)))
    template = await respond_template(client)
    body = run_body(version, template, n_responses=2, generator_setting={"kind": "none"})
    plan = await client.post(f"{RUNS}/plan", json=body)
    assert plan.status_code == 200, plan.text
    plan_out = plan.json()
    assert [w["code"] for w in plan_out["warnings"] if w["code"] == "generator_gguf"] == [
        "generator_gguf"
    ]
    (generator,) = plan_out["snapshots"]
    assert generator["kind"] == "none" and generator["set_hash"] is None
    (identity,) = plan_out["generator_identities"]
    assert identity == {"model_id": GGUF_MODEL, "revision": GGUF_REVISION, "set_hash": "none"}
    assert plan_out["model_revision"] == GGUF_REVISION

    start = await client.post(RUNS, json=body)
    assert start.status_code == 202, start.text
    before = len(gen.chats())
    run = gen.run_until_done(start.json()["id"])
    assert run.state == "completed", (run.error, run.failure_reason)
    chats = gen.chats()[before:]
    assert len(chats) == 8  # 4 seeds x 2 responses, every one answered
    for call in chats:
        # the field is ABSENT, not null and not the empty set
        assert "steering" not in call.body and "profile" not in call.body, call.body
        assert "steering_intensity" not in call.body
        assert call.headers["x-millm-strict"] == "true"
        assert call.body["model"] == GGUF_MODEL
    assert run.counts["respond:generated"] == 8

    records = records_of(run.id)
    assert len(records) == 8
    assert {r.outcome for r in records} == {"generated"}
    # unsteered, honestly: miServer reported `none` and the check matched it against `none`
    assert {r.steering_check for r in records} == {"match"}
    assert {r.reported_steering for r in records} == {"none"}
    assert {r.requested_set_hash for r in records} == {None}
    assert {r.model_id for r in records} == {GGUF_MODEL}
    assert {r.model_revision for r in records} == {GGUF_REVISION}

    (snapshot,) = snapshots_of(run.id)
    assert snapshot.kind == "none" and snapshot.set_hash is None
    assert snapshot.body_overrides == {}, "the snapshot records what was sent: no steering field"
    assert snapshot.model_id == GGUF_MODEL


async def test_a_gguf_reply_reporting_unknown_steering_is_discarded_not_recorded_unsteered(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    """The check is not weakened for GGUF: miServer reports ``unknown;reason=llamacpp_entries``
    when a live SAE entry exists beside a llama.cpp model, and such a row is never kept."""
    make_gguf(gen)
    gen.fake.attachments = [{"sae_id": "stray-sae", "layer": 3}]
    gen.fake.profiles["p"] = {"id": "p", "name": "p", "steering": {"1": 1.0}, "sae_id": "stray-sae"}
    gen.fake.active_profile_id = "p"
    version = make_version(version_table(texts(2)))
    template = await respond_template(client)
    start = await client.post(
        RUNS, json=run_body(version, template, generator_setting={"kind": "none"})
    )
    assert start.status_code == 202, start.text
    run = gen.run_until_done(start.json()["id"])
    records = records_of(run.id)
    assert records and {r.outcome for r in records} == {"discarded"}
    assert {r.reason_code for r in records} == {"steering_unreported"}
    assert {r.steering_check for r in records} == {"unreported"}
    assert {r.reported_steering for r in records} == {"unknown;reason=llamacpp_entries"}


@pytest.mark.parametrize(
    "setting",
    [
        {"kind": "inline", "sae_id": "s", "features": [{"index": 4127, "strength": 6.0}]},
        {"kind": "profile", "profile_name": "humor"},
    ],
    ids=["inline", "profile"],
)
async def test_a_steered_setting_on_a_gguf_generator_is_refused_at_plan(
    client: httpx.AsyncClient, gen: Gen, setting: dict[str, object]
) -> None:
    make_gguf(gen)
    version = make_version(version_table(texts(2)))
    template = await respond_template(client)
    before = len(gen.chats())
    for path in (f"{RUNS}/plan", RUNS):
        response = await client.post(
            path, json=run_body(version, template, generator_setting=setting)
        )
        assert response.status_code == 409, response.text
        error = response.json()["error"]
        assert error["code"] == "STEERING_UNSUPPORTED", error
        assert error["details"]["engine"] == "llamacpp" and error["details"]["model"] == GGUF_MODEL
        assert "GGUF" in error["message"]
    assert len(gen.chats()) == before


async def test_a_structured_output_template_on_a_gguf_generator_is_refused_at_plan(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    make_gguf(gen)
    created = await client.post(
        "/api/v1/generation-templates",
        json={
            "name": "respond-json",
            "kind": "respond",
            "body": {
                "prompt": "{prompt}",
                "structured_output": "json_schema",
                "json_schema": {"type": "object", "properties": {"a": {"type": "string"}}},
            },
        },
    )
    assert created.status_code == 201, created.text
    version = make_version(version_table(texts(2)))
    before = len(gen.chats())
    for path in (f"{RUNS}/plan", RUNS):
        response = await client.post(
            path,
            json=run_body(version, created.json()["id"], generator_setting={"kind": "none"}),
        )
        assert response.status_code == 409, response.text
        error = response.json()["error"]
        assert error["code"] == "STRUCTURED_OUTPUT_UNSUPPORTED", error
        assert error["details"]["templates"] == ["respond-json"]
    assert len(gen.chats()) == before


async def test_a_transformers_model_with_the_same_template_is_not_refused(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    """The structured-output refusal names GGUF only: the default (transformers) fake plans it."""
    created = await client.post(
        "/api/v1/generation-templates",
        json={
            "name": "respond-json-tf",
            "kind": "respond",
            "body": {
                "prompt": "{prompt}",
                "structured_output": "json_schema",
                "json_schema": {"type": "object"},
            },
        },
    )
    assert created.status_code == 201, created.text
    version = make_version(version_table(texts(2)))
    plan = await client.post(f"{RUNS}/plan", json=run_body(version, created.json()["id"]))
    assert plan.status_code == 200, plan.text
    assert "generator_gguf" not in {w["code"] for w in plan.json()["warnings"]}
