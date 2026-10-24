"""A behaviour key on every dataset object (017 FR-017.9, OQ-017-4 (a); FTASKS 3.1 - 3.3).

Recipes (the named head row), generation templates, generation runs, label runs and review queues
carry an optional key with the suite slug rule. Each object is created through its own REST route
and read back; the MCP tools' payloads are pinned by ``tests/support/mcp_ledger.py``. Two objects
that differ only in the key prove the key is not part of a hash.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy import text

from src.core.database import get_sync_engine
from src.schemas.behaviour import BEHAVIOUR_KEY_PATTERN
from tests.integration.labeling.test_judge_and_rederive import judge_body, judge_setup
from tests.support.generation_fixtures import (
    Gen,
    make_version,
    respond_template,
    run_body,
    texts,
    version_table,
)
from tests.support.labeling_fixtures import Labeling, set_operator
from tests.support.stub_operators import StubRegistry, body, install_stubs

API = "/api/v1"
KEYED = (
    "dw_recipes",
    "dw_generation_templates",
    "dw_generation_runs",
    "dw_label_runs",
    "dw_review_queues",
)
BAD = ("Pii", "-x", "x", "a" * 65, "key_with_underscore")
TEMPLATE = {"prompt": "Answer: {prompt}"}


@pytest.fixture
def stubs(monkeypatch: pytest.MonkeyPatch) -> StubRegistry:
    return install_stubs(monkeypatch)


def refused_naming_the_rule(response: httpx.Response, bad: str) -> None:
    assert response.status_code == 422, (bad, response.text)
    assert BEHAVIOUR_KEY_PATTERN in response.text, response.text


def test_the_five_partial_indexes_exist(migrated_database: None) -> None:
    """Read from the database catalogue: one index per table, on behaviour_key, partial."""
    with get_sync_engine().connect() as conn:
        rows = conn.execute(
            text(
                "SELECT tablename, indexdef FROM pg_indexes "
                "WHERE indexname = 'ix_' || tablename || '_behaviour_key' "
                "AND tablename = ANY(:tables)"
            ),
            {"tables": list(KEYED)},
        ).all()
    found = {r.tablename: r.indexdef for r in rows}
    assert set(found) == set(KEYED), found
    for table, definition in found.items():
        assert "(behaviour_key)" in definition, definition
        assert "WHERE (behaviour_key IS NOT NULL)" in definition, (table, definition)


async def test_recipes_keep_the_key_and_their_hash(
    client: httpx.AsyncClient, stubs: StubRegistry
) -> None:
    set_operator()
    recipe = body(("stub_keep", {}))
    for bad in BAD:
        refused_naming_the_rule(
            await client.post(
                f"{API}/recipes", json={"name": "k-bad", "body": recipe, "behaviour_key": bad}
            ),
            bad,
        )
    keyed = await client.post(
        f"{API}/recipes", json={"name": "k-one", "body": recipe, "behaviour_key": "key-one"}
    )
    plain = await client.post(f"{API}/recipes", json={"name": "k-two", "body": recipe})
    assert keyed.status_code == 201 and plain.status_code == 201, (keyed.text, plain.text)
    # the key is on the named recipe, never the body: one body, one hash under any key
    assert keyed.json()["head_hash"] == plain.json()["head_hash"]
    got = (await client.get(f"{API}/recipes/{keyed.json()['id']}")).json()
    assert got["behaviour_key"] == "key-one" and plain.json()["behaviour_key"] is None
    kept = await client.post(f"{API}/recipes/{got['id']}/clone", json={"name": "k-kept"})
    assert kept.status_code == 201 and kept.json()["behaviour_key"] == "key-one", kept.text
    moved = await client.post(
        f"{API}/recipes/{got['id']}/clone", json={"name": "k-moved", "behaviour_key": "key-two"}
    )
    assert moved.json()["behaviour_key"] == "key-two"
    cleared = await client.post(
        f"{API}/recipes/{got['id']}/clone", json={"name": "k-none", "behaviour_key": None}
    )
    assert cleared.json()["behaviour_key"] is None
    assert moved.json()["head_hash"] == keyed.json()["head_hash"]


async def test_generation_templates_keep_the_key_out_of_the_hash(
    client: httpx.AsyncClient,
) -> None:
    set_operator()
    for bad in BAD:
        refused_naming_the_rule(
            await client.post(
                f"{API}/generation-templates",
                json={"name": "t-bad", "kind": "respond", "body": TEMPLATE, "behaviour_key": bad},
            ),
            bad,
        )
    created = await client.post(
        f"{API}/generation-templates",
        json={"name": "t-keyed", "kind": "respond", "body": TEMPLATE, "behaviour_key": "key-one"},
    )
    assert created.status_code == 201, created.text
    row = created.json()
    from src.services.generation.template_service import template_hash

    assert row["content_hash"] == template_hash("t-keyed", 1, "respond", row["body"])
    got = (await client.get(f"{API}/generation-templates/{row['id']}")).json()
    assert got["behaviour_key"] == "key-one"
    kept = (await client.post(f"{API}/generation-templates/{row['id']}/clone", json={})).json()
    assert kept["behaviour_key"] == "key-one" and kept["version"] == 2
    moved = (
        await client.post(
            f"{API}/generation-templates/{row['id']}/clone", json={"behaviour_key": "key-two"}
        )
    ).json()
    assert moved["behaviour_key"] == "key-two"


async def test_a_generation_run_records_its_key_at_start(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    version = make_version(version_table(texts(4)))
    template = await respond_template(client)
    for bad in BAD:
        refused_naming_the_rule(
            await client.post(
                f"{API}/generation-runs", json=run_body(version, template, behaviour_key=bad)
            ),
            bad,
        )
    started = await client.post(
        f"{API}/generation-runs", json=run_body(version, template, behaviour_key="key-one")
    )
    assert started.status_code == 202, started.text
    run = (await client.get(f"{API}/generation-runs/{started.json()['id']}")).json()
    assert run["behaviour_key"] == "key-one" and run["behaviour_note"] is None
    assert run["behaviour"]["source"] == "run" and run["behaviour"]["key"] == "key-one"


async def test_a_label_run_and_its_review_queue_keep_the_key(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    version_id, rubric_id = await judge_setup(client, labeling, 6)
    for bad in BAD:
        refused_naming_the_rule(
            await client.post(
                f"{API}/label-runs", json=judge_body(version_id, rubric_id, behaviour_key=bad)
            ),
            bad,
        )
    started = await client.post(
        f"{API}/label-runs", json=judge_body(version_id, rubric_id, behaviour_key="key-one")
    )
    assert started.status_code == 201, started.text
    run_id = started.json()["id"]
    assert started.json()["behaviour_key"] == "key-one"
    labeling.run_until_done(run_id)
    assert (await client.get(f"{API}/label-runs/{run_id}")).json()["behaviour_key"] == "key-one"
    # a label review queue without a key takes its run's; one naming a key keeps it
    inherited = await client.post(
        f"{API}/review-queues", json={"kind": "label_review", "label_run_id": run_id, "size": 3}
    )
    assert inherited.status_code == 201, inherited.text
    assert inherited.json()["behaviour_key"] == "key-one"
    named = await client.post(
        f"{API}/review-queues",
        json={
            "kind": "calibration_labeling",
            "version_id": version_id,
            "question": "Is it so?",
            "label_set": ["yes", "no"],
            "size": 3,
            "behaviour_key": "key-two",
        },
    )
    assert named.status_code == 201, named.text
    got = (await client.get(f"{API}/review-queues/{named.json()['id']}")).json()
    assert got["behaviour_key"] == "key-two"
    for bad in BAD:
        refused_naming_the_rule(
            await client.post(
                f"{API}/review-queues",
                json={
                    "kind": "label_review",
                    "label_run_id": run_id,
                    "size": 3,
                    "behaviour_key": bad,
                },
            ),
            bad,
        )


async def test_the_runs_recorded_key_stands_and_a_rekeyed_rubric_is_named(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    """OQ-017-4 (a): the key recorded at start is authoritative; a rubric of the run's
    minimal-pair chain re-keyed AFTER the run is named beside it, and changes nothing."""
    from src.core.database import sync_session_factory
    from src.core.ids import new_id
    from src.models.minimal_pair_chain import MinimalPairChain
    from tests.integration.generation.test_run_context import RUBRIC

    version = make_version(version_table(texts(4)))
    template = await respond_template(client)
    rubric = await client.post(
        f"{API}/rubrics", json={"name": "ctx/key", "body": RUBRIC, "behaviour_key": "key-one"}
    )
    assert rubric.status_code == 201, rubric.text
    rubric_id = rubric.json()["id"]
    started = await client.post(
        f"{API}/generation-runs", json=run_body(version, template, behaviour_key="key-one")
    )
    run_id = started.json()["id"]
    with sync_session_factory()() as db:
        db.add(
            MinimalPairChain(
                id=new_id("mpc"),
                state="running",
                stage="generate",
                input_version_id=version,
                request={"rubric_id": rubric_id, "flip_from": "yes", "flip_to": "no"},
                generation_run_id=run_id,
                counts={},
                started_by="T",
                started_by_origin="operator",
                acting_by="T",
                acting_origin="operator",
            )
        )
        db.commit()

    def read(out: dict[str, Any]) -> tuple[str | None, str | None, list[str | None]]:
        return out["behaviour"]["key"], out["behaviour_note"], [b["key"] for b in out["behaviours"]]

    agreeing = (await client.get(f"{API}/generation-runs/{run_id}")).json()
    assert read(agreeing) == ("key-one", None, ["key-one", "key-one"])
    patched = await client.patch(f"{API}/rubrics/{rubric_id}", json={"behaviour_key": "key-two"})
    assert patched.status_code == 200, patched.text
    after = (await client.get(f"{API}/generation-runs/{run_id}")).json()
    key, note, keys = read(after)
    assert key == "key-one" and after["behaviour_key"] == "key-one"
    assert keys == ["key-one", "key-two"]
    assert note is not None and "'key-one'" in note and "'key-two'" in note, note
    assert "rubric ctx/key v1" in note
