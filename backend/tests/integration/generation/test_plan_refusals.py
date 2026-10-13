"""Every plan refusal leaves no run, no file and no job (007 FTASKS 4.3, 5.4, 5.5, 5.7, 5.8, 5.10,
5.12)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy import text

from src.core.database import sync_session_factory
from tests.support.generation_fixtures import (
    SAE,
    Gen,
    make_version,
    respond_template,
    run_body,
    version_table,
)

RUNS = "/api/v1/generation-runs"


def counts() -> tuple[int, int, int]:
    with sync_session_factory()() as db:
        return (
            db.execute(text("SELECT count(*) FROM dw_generation_runs")).scalar_one(),
            db.execute(text("SELECT count(*) FROM dw_steering_snapshots")).scalar_one(),
            db.execute(
                text("SELECT count(*) FROM dw_jobs WHERE kind = 'generation_run'")
            ).scalar_one(),
        )


async def refused(
    client: httpx.AsyncClient, body: dict[str, Any], status: int, code: str
) -> dict[str, Any]:
    for path in (f"{RUNS}/plan", RUNS):
        response = await client.post(path, json=body)
        assert response.status_code == status, (path, response.text)
        assert response.json()["error"]["code"] == code, response.text
    assert counts() == (0, 0, 0)
    return dict(response.json()["error"])


async def test_a_version_without_a_held_out_split_is_refused(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    version = make_version(version_table(["a", "b"]), held_out=False)
    error = await refused(
        client, run_body(version, await respond_template(client)), 409, "HELD_OUT_MISSING"
    )
    assert error["details"]["next_step"]["operator"] == "split"


async def test_seed_splits_including_the_held_out_split_are_refused(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    version = make_version(version_table(["a", "b"]))
    body = run_body(version, await respond_template(client), seed_splits=["train", "test"])
    error = await refused(client, body, 422, "HELD_OUT_SEED")
    assert error["details"]["held_out_seed_splits"] == ["test"]


async def test_an_unknown_profile_is_refused_and_never_created(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    version = make_version(version_table(["a"]))
    body = run_body(
        version,
        await respond_template(client),
        generator_setting={"kind": "profile", "profile_name": "absent"},
    )
    await refused(client, body, 409, "PROFILE_NOT_FOUND")
    writes = [r for r in gen.fake.requests if r.path.startswith("/api/") and r.method != "GET"]
    assert writes == [], "007 never writes miLLM state"


async def test_an_unattached_sae_is_refused(client: httpx.AsyncClient, gen: Gen) -> None:
    version = make_version(version_table(["a"]))
    setting = {
        "kind": "inline",
        "sae_id": "not-attached",
        "features": [{"index": 1, "strength": 2.0}],
    }
    body = run_body(version, await respond_template(client), generator_setting=setting)
    error = await refused(client, body, 409, "SAE_NOT_ATTACHED")
    assert error["details"]["attached"] == [SAE]


async def test_two_differing_indices_are_refused(client: httpx.AsyncClient, gen: Gen) -> None:
    version = make_version(
        version_table(["a"]),
        target_type="dpo",
        roles={"prompt": "content", "chosen": "content", "rejected": "content"},
    )
    a = {
        "kind": "inline",
        "sae_id": SAE,
        "features": [{"index": 1, "strength": 2.0}, {"index": 2, "strength": 1.0}],
    }
    b = {
        "kind": "inline",
        "sae_id": SAE,
        "features": [{"index": 1, "strength": 3.0}, {"index": 2, "strength": 2.0}],
    }
    body = run_body(
        version,
        await respond_template(client),
        mode="steered_pairs",
        setting_a=a,
        setting_b=b,
        chosen_side="b",
        target_type="dpo",
    )
    error = await refused(client, body, 422, "NOT_ONE_AXIS")
    assert [d["index"] for d in error["details"]["differing"]] == [1, 2]


async def test_steering_is_refused_while_028_is_unmet(
    client: httpx.AsyncClient, gen: Gen, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.core.config import get_settings

    monkeypatch.setattr(get_settings(), "millm_steering_supported", False)
    version = make_version(version_table(["a"]))
    setting = {"kind": "inline", "sae_id": SAE, "features": [{"index": 1, "strength": 2.0}]}
    body = run_body(version, await respond_template(client), generator_setting=setting)
    await refused(client, body, 409, "STEERING_UNSUPPORTED")


async def test_steering_is_refused_on_a_non_millm_endpoint(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    from tests.support.fake_openai import FakeOpenAI
    from tests.support.generation_fixtures import set_role

    with FakeOpenAI() as upstream:
        set_role("generation", upstream.base_url, "m1")
        version = make_version(version_table(["a"]))
        setting = {"kind": "profile", "profile_name": "humor"}
        body = run_body(version, await respond_template(client), generator_setting=setting)
        await refused(client, body, 409, "STEERING_UNSUPPORTED")


async def test_a_judge_inheriting_the_generator_is_refused_naming_the_inheritance(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    from tests.support.generation_fixtures import GEN_MODEL, set_role

    set_role("judge", gen.base_url, GEN_MODEL)
    set_role("generation", gen.base_url, GEN_MODEL, inherit=True)
    version = make_version(version_table(["a"]))
    error = await refused(
        client, run_body(version, await respond_template(client)), 422, "JUDGE_IS_GENERATOR"
    )
    assert error["details"]["inherited_from"] == "judge"
    assert "inherits the judge" in error["message"]


async def test_a_separate_generation_model_is_allowed(client: httpx.AsyncClient, gen: Gen) -> None:
    version = make_version(version_table(["a"]))
    plan = await client.post(f"{RUNS}/plan", json=run_body(version, await respond_template(client)))
    assert plan.status_code == 200, plan.text
    assert plan.json()["independence"] == "independent"


async def test_the_same_model_with_a_steering_set_is_allowed(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    from tests.support.generation_fixtures import GEN_MODEL, set_role

    set_role("judge", gen.base_url, GEN_MODEL)
    version = make_version(version_table(["a"]))
    setting = {"kind": "inline", "sae_id": SAE, "features": [{"index": 1, "strength": 2.0}]}
    plan = await client.post(
        f"{RUNS}/plan",
        json=run_body(version, await respond_template(client), generator_setting=setting),
    )
    assert plan.status_code == 200, plan.text


async def test_template_placeholders_are_checked_against_the_version(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    created = await client.post(
        "/api/v1/generation-templates",
        json={
            "name": "respond-topic",
            "kind": "respond",
            "body": {"prompt": "{prompt} about {topic}"},
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["placeholders"] == ["prompt", "topic"]
    version = make_version(version_table(["a"]))
    error = await refused(
        client, run_body(version, created.json()["id"]), 422, "TEMPLATE_PLACEHOLDER_UNKNOWN"
    )
    assert error["details"]["missing"] == ["topic"]


async def test_a_template_hash_is_stable_and_a_used_template_refuses_updates(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    from sqlalchemy.exc import DBAPIError

    from src.services.generation.template_service import template_hash

    body = {
        "prompt": "{prompt}",
        "system": None,
        "sampling": {"temperature": 0.8, "top_p": 1.0, "max_tokens": 512},
        "structured_output": "none",
        "json_schema": None,
    }
    assert template_hash("t", 1, "respond", body) == template_hash(
        "t", 1, "respond", dict(reversed(list(body.items())))
    )
    template = await respond_template(client)
    version = make_version(version_table(["a"]))
    assert (await client.post(RUNS, json=run_body(version, template))).status_code == 202
    got = await client.get(f"/api/v1/generation-templates/{template}")
    assert got.json()["used"] is True
    with sync_session_factory()() as db, pytest.raises(DBAPIError, match="TEMPLATE_IMMUTABLE"):
        db.execute(
            text("UPDATE dw_generation_templates SET description = 'x' WHERE id = :i"),
            {"i": template},
        )
        db.commit()
    clone = await client.post(f"/api/v1/generation-templates/{template}/clone", json={})
    assert clone.status_code == 201 and clone.json()["version"] == 2


async def test_no_007_route_takes_a_secret_and_all_are_served(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    from src.main import fastapi_app

    spec = fastapi_app.openapi()
    paths = {
        p: v
        for p, v in spec["paths"].items()
        if "generation" in p or "steering-settings" in p or "diversity" in p
    }
    assert len(paths) == 15
    text_schema = str(spec["components"]["schemas"].get("GenerationRunCreate")) + str(
        spec["components"]["schemas"].get("PreviewRequest")
    )
    for word in ("api_key", "token", "secret", "password"):
        assert word not in text_schema


async def test_the_preview_writes_nothing(
    client: httpx.AsyncClient, gen: Gen, data_dir: Any
) -> None:
    def files() -> set[str]:
        return {str(p) for p in data_dir.rglob("*") if p.is_file()}

    before_files = files()
    with sync_session_factory()() as db:
        tables = [
            r[0]
            for r in db.execute(
                text("SELECT tablename FROM pg_tables WHERE tablename LIKE 'dw_%'")
            ).all()
        ]
        before = {
            t: db.execute(text(f'SELECT count(*) FROM "{t}"')).scalar_one() for t in tables
        }  # noqa: S608
    response = await client.post(
        f"{RUNS}/preview", json={"prompts": ["one", "two"], "seed": 3, "setting": {"kind": "none"}}
    )
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [i["steering_check"] for i in items] == ["match", "match"]
    assert all(i["reported_steering"] == "none" and i["seed_confirmed"] for i in items)
    calls = gen.chats()
    assert len(calls) == 2 and all("x-millm-lease" not in c.headers for c in calls)
    assert all(c.headers["x-millm-load-policy"] == "refuse" for c in calls)
    with sync_session_factory()() as db:
        after = {
            t: db.execute(text(f'SELECT count(*) FROM "{t}"')).scalar_one() for t in tables
        }  # noqa: S608
    changed = {t for t in tables if before[t] != after[t] and t not in ("dw_agent_requests",)}
    assert changed == set(), changed
    assert files() == before_files


async def test_a_preview_of_more_than_five_prompts_is_refused(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    response = await client.post(f"{RUNS}/preview", json={"prompts": [str(i) for i in range(6)]})
    assert response.status_code == 422 and response.json()["error"]["code"]


async def test_pagination_and_envelopes(client: httpx.AsyncClient, gen: Gen) -> None:
    missing = await client.get(f"{RUNS}/gr_nope")
    assert (
        missing.status_code == 404 and missing.json()["error"]["code"] == "GENERATION_RUN_NOT_FOUND"
    )
    for path in (f"{RUNS}/gr_nope/records", f"{RUNS}/gr_nope/pairs"):
        assert (await client.get(path)).json()["error"]["code"] == "GENERATION_RUN_NOT_FOUND"
    bad = await client.get(RUNS, params={"limit": 999})
    assert bad.status_code == 422 and "error" in bad.json()
    listed = await client.get(RUNS, params={"page": 1, "limit": 5})
    assert listed.status_code == 200 and listed.json() == {
        "items": [],
        "total": 0,
        "page": 1,
        "limit": 5,
    }
    templates = await client.get("/api/v1/generation-templates", params={"limit": 1})
    # expand-v1, respond-v1 and 009's minimal-pair-v1
    assert templates.json()["total"] == 3 and len(templates.json()["items"]) == 1
    nope = await client.get("/api/v1/versions/00000000-0000-0000-0000-000000000000/diversity")
    assert nope.status_code == 404 and nope.json()["error"]["code"] == "DIVERSITY_REPORT_NOT_FOUND"
    extra = await client.post(f"{RUNS}/plan", json={"unknown_field": 1})
    assert extra.status_code == 422 and "error" in extra.json()
