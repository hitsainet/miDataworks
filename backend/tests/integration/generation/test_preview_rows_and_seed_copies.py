"""Two silent generation defects found on the live app on 2026-10-08, pinned through the routes.

D1 — the preview rendered every placeholder except ``{prompt}`` EMPTY: a template whose system
message reads ``{stake_style}`` previewed with the stake style deleted, which no run ever sends. A
preview now draws REAL seed rows (chosen and rendered by the run's own code), and bare prompts are
refused (``PREVIEW_NEEDS_ROWS``) for a template that reads anything besides ``{prompt}``.

D2 — seeds are chosen by row key, so copies of a key collapse to one seed and a metadata
placeholder takes the first copy's value. 299 of 3,000 live keys had copies whose ``stake_style``
differed and the plan said nothing. The plan now warns ``seed_copies_disagree``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx
import pyarrow as pa
from sqlalchemy import text

from src.core.database import sync_session_factory
from src.services.row_keys import compute_row_key
from tests.support.generation_fixtures import ROLES, Gen, make_version, run_body

RUNS = "/api/v1/generation-runs"
STYLE_ROLES = {**ROLES, "stake_style": "metadata", "note": "metadata"}


def seed_table(rows: Sequence[tuple[str, str, str | None, str, int]]) -> pa.Table:
    """``(prompt, split, stake_style, note, occurrence)`` rows keyed on content only, as 002 keys
    them — rows with the same prompt are COPIES of one key."""
    out: list[dict[str, Any]] = []
    for prompt, split, style, note, occurrence in rows:
        row: dict[str, Any] = {"prompt": prompt, "completion": f"answer to {prompt}"}
        row["_dw_row_key"] = compute_row_key(row, sorted(ROLES))
        row.update(
            {
                "_dw_occurrence": occurrence,
                "_dw_split": split,
                "_dw_origin": "source",
                "_dw_source_id": None,
                "_dw_source_locator": None,
                "_dw_parent_keys": None,
                "stake_style": style,
                "note": note,
            }
        )
        out.append(row)
    schema = pa.schema(
        [
            ("prompt", pa.string()),
            ("completion", pa.string()),
            ("_dw_row_key", pa.string()),
            ("_dw_occurrence", pa.int32()),
            ("_dw_split", pa.string()),
            ("_dw_origin", pa.string()),
            ("_dw_source_id", pa.string()),
            ("_dw_source_locator", pa.string()),
            ("_dw_parent_keys", pa.list_(pa.string())),
            ("stake_style", pa.string()),
            ("note", pa.string()),
        ]
    )
    return pa.Table.from_pylist(out, schema=schema)


def distinct_rows(n: int) -> list[tuple[str, str, str | None, str, int]]:
    rows = [(f"question {i:03d}", "train", f"style-{i:03d}", "n", 0) for i in range(n)]
    rows += [("held-out one", "test", "held", "n", 0), ("held-out two", "test", "held", "n", 0)]
    return rows


async def style_template(client: httpx.AsyncClient, system: str = "Style: {stake_style}") -> str:
    created = await client.post(
        "/api/v1/generation-templates",
        json={
            "name": f"respond-style-{abs(hash(system)) % 10_000}",
            "kind": "respond",
            "body": {"system": system, "prompt": "{prompt}"},
        },
    )
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


def dw_counts() -> dict[str, int]:
    with sync_session_factory()() as db:
        tables = [
            r[0]
            for r in db.execute(
                text("SELECT tablename FROM pg_tables WHERE tablename LIKE 'dw_%'")
            ).all()
        ]
        return {
            t: db.execute(text(f'SELECT count(*) FROM "{t}"')).scalar_one()  # noqa: S608
            for t in tables
            if t != "dw_agent_requests"
        }


# --- D1: the preview is faithful -----------------------------------------------------------


async def test_bare_prompts_are_refused_for_a_template_reading_another_placeholder(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    template = await style_template(client)
    response = await client.post(
        f"{RUNS}/preview",
        json={"prompts": ["one", "two"], "respond_template_id": template, "seed": 3},
    )
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "PREVIEW_NEEDS_ROWS"
    assert error["details"] == {"missing": ["stake_style"]}
    assert "never renders a placeholder empty" in error["message"]
    assert gen.chats() == [], "nothing is sent with a placeholder rendered empty"


async def test_bare_prompts_still_preview_a_prompt_only_template(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    """The frontend's contract: ``preview(prompts, setting, respond_template_id)``."""
    template = await style_template(client, system="Be brief.")
    response = await client.post(
        f"{RUNS}/preview",
        json={"prompts": ["one"], "respond_template_id": template, "setting": {"kind": "none"}},
    )
    assert response.status_code == 200, response.text
    (call,) = gen.chats()
    assert call.body["messages"] == [
        {"role": "system", "content": "Be brief."},
        {"role": "user", "content": "one"},
    ]
    item = response.json()["items"][0]
    assert item["prompt"] == "one" and item["row_key"] is None
    assert response.json()["selection_seed"] is None


async def test_a_preview_from_seed_rows_sends_exactly_what_the_run_sends(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    version = make_version(seed_table(distinct_rows(12)), roles=STYLE_ROLES)
    template = await style_template(client)
    before = dw_counts()
    preview = await client.post(
        f"{RUNS}/preview",
        json={
            "input_version_id": version,
            "prompt_column": "prompt",
            "seed_splits": ["train"],
            "sample_size": 3,
            "respond_template_id": template,
            "seed": 11,
        },
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["selection_seed"] == 11
    preview_calls = gen.chats()
    assert len(preview_calls) == 3
    assert dw_counts() == before, "a preview writes no row"
    items = preview.json()["items"]
    for item, call in zip(items, preview_calls, strict=True):
        n = item["prompt"].split()[-1]
        assert call.body["messages"] == [
            {"role": "system", "content": f"Style: style-{n}"},
            {"role": "user", "content": f"question {n}"},
        ]
        assert item["row_key"] and len(item["row_key"]) == 64

    # the run with the same seed and size draws the same rows and sends the same messages
    started = await client.post(RUNS, json=run_body(version, template, sample_size=3, seed=11))
    assert started.status_code == 202, started.text
    run = gen.run_until_done(started.json()["id"])
    assert run.state == "completed", run.error
    run_calls = gen.chats()[3:]
    assert [c.body["messages"] for c in run_calls] == [c.body["messages"] for c in preview_calls]


async def test_a_seed_row_preview_keeps_the_runs_guards(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    version = make_version(seed_table(distinct_rows(4)), roles=STYLE_ROLES)
    template = await style_template(client)
    base = {"input_version_id": version, "prompt_column": "prompt", "respond_template_id": template}
    held = await client.post(f"{RUNS}/preview", json={**base, "seed_splits": ["train", "test"]})
    assert held.status_code == 422 and held.json()["error"]["code"] == "HELD_OUT_SEED", held.text
    unknown = await client.post(f"{RUNS}/preview", json={**base, "seed_splits": ["nope"]})
    assert unknown.json()["error"]["code"] == "SEED_SPLIT_UNKNOWN", unknown.text
    absent = await style_template(client, system="Tone: {tone}")
    missing = await client.post(
        f"{RUNS}/preview", json={**base, "seed_splits": ["train"], "respond_template_id": absent}
    )
    assert missing.status_code == 422, missing.text
    assert missing.json()["error"]["code"] == "TEMPLATE_PLACEHOLDER_UNKNOWN"
    assert missing.json()["error"]["details"]["missing"] == ["tone"]
    both = await client.post(
        f"{RUNS}/preview", json={**base, "prompts": ["x"], "seed_splits": ["train"]}
    )
    assert both.status_code == 422 and both.json()["error"]["code"] == "VALIDATION_ERROR", both.text
    assert gen.chats() == []


async def test_a_run_whose_system_names_an_absent_column_is_refused_at_plan(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    version = make_version(seed_table(distinct_rows(4)), roles=STYLE_ROLES)
    template = await style_template(client, system="Tone: {tone} / {stake_style}")
    for path in (f"{RUNS}/plan", RUNS):
        response = await client.post(path, json=run_body(version, template))
        assert response.status_code == 422, response.text
        error = response.json()["error"]
        assert error["code"] == "TEMPLATE_PLACEHOLDER_UNKNOWN"
        assert error["details"]["missing"] == ["tone"]
    with sync_session_factory()() as db:
        assert db.execute(text("SELECT count(*) FROM dw_generation_runs")).scalar_one() == 0


# --- D2: copies of a row key ----------------------------------------------------------------


def copies_rows() -> list[tuple[str, str, str | None, str, int]]:
    rows = distinct_rows(6)
    # three keys with two copies each: two disagree on stake_style, one agrees
    rows += [
        ("question 000", "train", "other-000", "n", 1),
        ("question 001", "train", None, "n", 1),
        ("question 002", "train", "style-002", "different note", 1),
    ]
    return rows


async def test_the_plan_warns_when_copies_disagree_on_a_column_the_template_reads(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    version = make_version(seed_table(copies_rows()), roles=STYLE_ROLES)
    template = await style_template(client)
    plan = await client.post(f"{RUNS}/plan", json=run_body(version, template, sample_size=10))
    assert plan.status_code == 200, plan.text
    out = plan.json()
    # selection works on KEYS, and so does the count it is offered from
    assert out["seed_rows_available"] == 6 and out["seed_rows_selected"] == 6
    (warning,) = [w for w in out["warnings"] if w["code"] == "seed_copies_disagree"]
    assert warning["keys_affected"] == 2  # a NULL beside a value disagrees; the note is not read
    assert warning["columns"] == {"stake_style": 2}
    assert warning["keys"] == 6 and warning["rows"] == 9
    assert "ONE copy per row key" in warning["message"]
    assert "identifying content column" in warning["message"]
    started = await client.post(RUNS, json=run_body(version, template, sample_size=10))
    assert started.status_code == 202, started.text
    assert "seed_copies_disagree" in {w["code"] for w in started.json()["warnings"]}
    # the run seeds the first copy: one request per key, with the first copy's style
    run = gen.run_until_done(started.json()["id"])
    assert run.state == "completed", run.error
    systems = sorted(c.body["messages"][0]["content"] for c in gen.chats())
    assert systems == [f"Style: style-{i:03d}" for i in range(6)]


async def test_no_warning_when_copies_differ_only_in_columns_the_run_does_not_read(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    rows = distinct_rows(6) + [("question 002", "train", "style-002", "different note", 1)]
    version = make_version(seed_table(rows), roles=STYLE_ROLES)
    template = await style_template(client)
    plan = await client.post(f"{RUNS}/plan", json=run_body(version, template))
    assert plan.status_code == 200, plan.text
    assert "seed_copies_disagree" not in {w["code"] for w in plan.json()["warnings"]}
    # ...while a template that DOES read the note warns about exactly that column
    noted = await style_template(client, system="Note: {note}")
    plan = await client.post(f"{RUNS}/plan", json=run_body(version, noted))
    (warning,) = [w for w in plan.json()["warnings"] if w["code"] == "seed_copies_disagree"]
    assert warning["columns"] == {"note": 1} and warning["keys_affected"] == 1
