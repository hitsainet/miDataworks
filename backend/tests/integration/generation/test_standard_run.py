"""A standard, unsteered run end to end against the fake miLLM (007 FTASKS 6.11, 5.x).

Records, chunks and counts are correct; the generated text lives ONLY in the chunk Parquet; every
request carries the strict and refuse-load headers and the explicit unsteered body.
"""

from __future__ import annotations

from typing import Any

import httpx
import pyarrow.parquet as pq
import pytest
from sqlalchemy import select, text

from src.core.database import sync_session_factory
from src.core.storage import resolve_under_data_dir
from src.models.generation import GenerationChunk, GenerationRecord
from tests.support.generation_fixtures import (
    GEN_MODEL,
    REVISION,
    Gen,
    make_version,
    respond_template,
    run_body,
    texts,
    version_table,
)

RUNS = "/api/v1/generation-runs"


async def start(client: httpx.AsyncClient, body: dict[str, Any]) -> dict[str, Any]:
    response = await client.post(RUNS, json=body)
    assert response.status_code == 202, response.text
    return dict(response.json())


async def test_a_standard_run_records_every_response(
    client: httpx.AsyncClient, gen: Gen, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.core.config import get_settings

    monkeypatch.setattr(get_settings(), "generation_chunk_size", 3)
    version = make_version(version_table(texts(8)))
    template = await respond_template(client)
    plan = await client.post(f"{RUNS}/plan", json=run_body(version, template, n_responses=2))
    assert plan.status_code == 200, plan.text
    assert plan.json()["expected_requests"] == 8 and plan.json()["held_out"]["splits"] == ["test"]
    with sync_session_factory()() as db:
        assert db.execute(text("SELECT count(*) FROM dw_generation_runs")).scalar_one() == 0
    out = await start(client, run_body(version, template, n_responses=2))
    assert out["state"] == "queued" and out["started_by"] == "Test Operator"
    before = len(gen.chats())
    run = gen.run_until_done(out["id"])
    assert run.state == "completed", run.error
    chats = gen.chats()[before:]
    assert len(chats) == 8  # 4 seeds x 2 responses
    for call in chats:
        assert call.headers["x-millm-strict"] == "true"
        assert call.headers["x-millm-load-policy"] == "refuse"
        assert call.body["steering"] == {"features": []}, "explicit unsteered body (FR-28.2.2)"
        assert call.body["model"] == GEN_MODEL and isinstance(call.body["seed"], int)
    assert run.counts["respond:generated"] == 8 and run.pinned is True
    with sync_session_factory()() as db:
        records = list(
            db.execute(
                select(GenerationRecord)
                .where(GenerationRecord.run_id == run.id)
                .order_by(GenerationRecord.record_index)
            ).scalars()
        )
        chunks = list(
            db.execute(select(GenerationChunk).where(GenerationChunk.run_id == run.id)).scalars()
        )
    assert [r.record_index for r in records] == list(range(8))
    assert {r.steering_check for r in records} == {"match"}
    assert {r.reported_steering for r in records} == {"none"}
    assert {r.model_revision for r in records} == {REVISION}
    assert all(r.seed_confirmed is True for r in records)
    assert len(chunks) == 2 and sum(c.row_count for c in chunks) == 8  # 3 prompts x 2, then 1 x 2
    # the text is in the Parquet only
    columns = {c.name for c in GenerationRecord.__table__.columns}
    assert "text" not in columns and "prompt" not in columns
    first = pq.read_table(resolve_under_data_dir(chunks[0].path)).to_pylist()
    assert all(r["text"].startswith("Reply to [") for r in first)
    page = await client.get(f"{RUNS}/{run.id}/records", params={"outcome": "generated"})
    assert page.status_code == 200 and page.json()["total"] == 8
    assert page.json()["items"][0]["text"].startswith("Reply to [")


async def test_an_agent_start_runs_at_once_with_its_identity(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    version = make_version(version_table(texts(3)))
    template = await respond_template(client)
    response = await client.post(
        RUNS, json=run_body(version, template), headers={"X-Dataworks-Agent": "agent:claude"}
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["started_by"] == "agent:claude" and body["started_by_origin"] == "agent"
    assert body["job_ids"] and "approval_id" not in body


async def test_a_prompt_only_run_stops_after_expansion(client: httpx.AsyncClient, gen: Gen) -> None:
    from tests.support.generation_fixtures import expand_template

    version = make_version(
        version_table(texts(3)),
        target_type="grpo_prompt",
        roles={"prompt": "content", "completion": "metadata"},
    )
    expand = await expand_template(client)
    out = await start(
        client,
        {
            "input_version_id": version,
            "prompt_column": "prompt",
            "seed_splits": ["train"],
            "sample_size": 3,
            "seed": 1,
            "expand_template_id": expand,
            "target_type": "grpo_prompt",
        },
    )
    assert out["stages"] == ["seed", "expand"]
    run = gen.run_until_done(out["id"])
    assert run.state == "completed", run.error
    assert run.counts.get("expand:generated") == 3 and "respond:generated" not in run.counts
