"""MCP gap G-b (2026-11-25): a generation run can be dry-run before the model swap.

``dataworks_plan_generation`` refused ``MODEL_NOT_LOADED`` whenever the run's model was not the one
resident in miServer, so FU-7a could not check a Gemma or Llama run until the GPU had been swapped.
``assume_model`` plans as if the run's own model were resident: every other check runs, and
``assumption.skipped`` names what needs the loaded model (always the served name, ENH-006 D71).
It starts and loads nothing, and a start cannot carry it. Driven over REST and through the
PRODUCTION MCP registry against the fake miServer, on a real PostgreSQL.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy import text

from src.core.database import sync_session_factory
from tests.support.generation_fixtures import (
    GEN_MODEL,
    Gen,
    make_version,
    respond_template,
    run_body,
    version_table,
)
from tests.support.mcp_live import call, production_mcp, refusal_of

RUNS = "/api/v1/generation-runs"
OTHER = {
    "id": 12,
    "name": "Other-Resident-8B",
    "repo_id": "example/other",
    "revision": "1" * 40,
    "quantization": "FP16",
}
GEN_REVISION = "a" * 40
GEN_ROW = {"id": 31, "name": GEN_MODEL, "repo_id": "example/gen-model-7b", "revision": GEN_REVISION}


def counts() -> tuple[int, int, int]:
    with sync_session_factory()() as db:
        return (
            db.execute(text("SELECT count(*) FROM dw_generation_runs")).scalar_one(),
            db.execute(text("SELECT count(*) FROM dw_steering_snapshots")).scalar_one(),
            db.execute(
                text("SELECT count(*) FROM dw_jobs WHERE kind = 'generation_run'")
            ).scalar_one(),
        )


def writes_to(gen: Gen) -> list[tuple[str, str]]:
    """Every request to miServer that is not a read: a load, a lease, a generation."""
    return [(r.method, r.path) for r in gen.fake.requests if r.method != "GET"]


async def body_for(client: httpx.AsyncClient, **extra: Any) -> dict[str, Any]:
    version = make_version(version_table([f"q{i}" for i in range(6)]))
    return run_body(version, await respond_template(client), **extra)


async def test_a_plan_for_a_model_not_resident_is_refused_without_the_assumption(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    """The gap, as FU-7a met it: the control for every test below."""
    gen.fake.resident = OTHER
    gen.fake.extra_models = [GEN_ROW]
    response = await client.post(f"{RUNS}/plan", json=await body_for(client))
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "MODEL_NOT_LOADED"


async def test_assume_model_plans_every_other_check_and_names_the_skipped(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    gen.fake.resident = OTHER
    gen.fake.extra_models = [GEN_ROW]
    body = await body_for(client, assume_model=GEN_MODEL)
    response = await client.post(f"{RUNS}/plan", json=body)
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["assumption"] == {
        "model_id": GEN_MODEL,
        "resident_now": OTHER["name"],
        "assumed": True,
        "listed": True,
        "skipped": [plan["assumption"]["skipped"][0]],
    }
    assert plan["assumption"]["skipped"][0]["check"] == "served_name"
    assert "D71" in plan["assumption"]["skipped"][0]["why"]
    # The checks that need no loaded model still ran: the listed revision is in the identity.
    assert plan["model_revision"] == GEN_REVISION
    assert plan["generator_identities"][0]["revision"] == GEN_REVISION
    assert plan["resident_model"] == OTHER["name"]
    (warning,) = [w for w in plan["warnings"] if w["code"] == "model_assumed"]
    assert "served_name" in warning["message"] and "Nothing was started" in warning["message"]
    assert plan["seed_rows_selected"] == 4 and plan["independence"] == "independent"
    # Nothing was started, written or loaded.
    assert counts() == (0, 0, 0)
    assert writes_to(gen) == []


async def test_a_model_miserver_does_not_list_skips_its_revision_and_engine(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    gen.fake.resident = OTHER
    response = await client.post(
        f"{RUNS}/plan", json=await body_for(client, assume_model=GEN_MODEL)
    )
    assert response.status_code == 200, response.text
    assumption = response.json()["assumption"]
    assert assumption["listed"] is False
    assert [s["check"] for s in assumption["skipped"]] == [
        "served_name",
        "model_revision",
        "gguf_engine",
    ]
    assert response.json()["model_revision"] is None


async def test_the_assumption_must_be_the_runs_own_model(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    gen.fake.resident = OTHER
    gen.fake.extra_models = [GEN_ROW]
    response = await client.post(
        f"{RUNS}/plan", json=await body_for(client, assume_model="Qwen2.5-7B")
    )
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "assume_model_mismatch"
    assert error["details"] == {"needed": GEN_MODEL, "assume_model": "Qwen2.5-7B"}


async def test_assuming_the_resident_model_assumes_nothing(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    response = await client.post(
        f"{RUNS}/plan", json=await body_for(client, assume_model=GEN_MODEL)
    )
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["assumption"] == {
        "model_id": GEN_MODEL,
        "resident_now": GEN_MODEL,
        "assumed": False,
        "skipped": [],
    }
    assert not [w for w in plan["warnings"] if w["code"] == "model_assumed"]


async def test_a_start_cannot_carry_the_assumption(client: httpx.AsyncClient, gen: Gen) -> None:
    gen.fake.resident = OTHER
    gen.fake.extra_models = [GEN_ROW]
    response = await client.post(RUNS, json=await body_for(client, assume_model=GEN_MODEL))
    assert response.status_code == 422, response.text
    assert counts() == (0, 0, 0) and writes_to(gen) == []


async def test_the_mcp_plan_sends_the_assumption_and_the_start_refuses_it(
    client: httpx.AsyncClient, gen: Gen, monkeypatch: pytest.MonkeyPatch
) -> None:
    gen.fake.resident = OTHER
    gen.fake.extra_models = [GEN_ROW]
    body = await body_for(client)
    mcp, tool_client = production_mcp(monkeypatch)
    posts: list[tuple[str, Any]] = []
    original = tool_client.post

    async def recorded(path: str, *args: Any, **kwargs: Any) -> Any:
        posts.append((path, kwargs.get("json_body")))
        return await original(path, *args, **kwargs)

    monkeypatch.setattr(tool_client, "post", recorded)
    args = {k: body[k] for k in ("input_version_id", "prompt_column", "seed_splits", "sample_size")}
    args |= {"target_type": "sft", "seed": 11, "respond_template_id": body["respond_template_id"]}

    planned = await call(mcp, "dataworks_plan_generation", {**args, "assume_model": GEN_MODEL})
    plan = planned.structured_content
    assert plan["assumption"]["assumed"] is True
    assert [p for p, _ in posts] == ["/generation-runs/plan"]
    assert posts[0][1]["assume_model"] == GEN_MODEL
    # Without the argument the tool sends no assumption, and the plan refuses as before.
    with pytest.raises(ToolError) as refused:
        await call(mcp, "dataworks_plan_generation", args)
    assert refusal_of(refused.value)[:2] == ("MODEL_NOT_LOADED", 409)
    assert "assume_model" not in posts[1][1]
    # The start tool does not declare it: refused by name before any request.
    with pytest.raises(ToolError) as unknown:
        await call(mcp, "dataworks_start_generation", {**args, "assume_model": GEN_MODEL})
    assert "unknown argument(s) assume_model" in str(unknown.value)
    assert len(posts) == 2 and counts() == (0, 0, 0) and writes_to(gen) == []
