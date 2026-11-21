"""D3 (live, 2026-10-08): a rubric whose messages cannot render was ACCEPTED, then failed 20 rows
into a judge run (``ENDPOINT_FAILING ... ROW_ERROR: the rubric could not be rendered: '"score"'``).

Every message is now rendered at create, clone and import through the worker's own function
(``openai_chat_judge.render_rubric_message``), and the label-run plan (and so its start) re-checks a
STORED rubric, so one saved before this check fails at plan, not after 20 rows.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from src.core.database import sync_session_factory
from src.models.rubric import Rubric
from src.services.decision_template_service import library_hash
from tests.integration.labeling.helpers import texts
from tests.support.labeling_fixtures import Labeling, make_version, set_operator, set_role

API = "/api/v1/rubrics"
JSON_SYSTEM = 'Reply with ONLY compact JSON: {"score": <1-10>, "reason": "<short>"}'


@pytest.fixture(autouse=True)
def _operator(labeling: Labeling) -> None:
    set_operator()


def body(*messages: tuple[str, str], **over: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "style": "pointwise",
        "messages": [{"role": r, "content": c} for r, c in messages],
        "input_fields": ["text"],
        "parser": "verdict_line_v1",
        "allowed_verdicts": ["yes", "no"],
    }
    out.update(over)
    return out


def rubric_rows() -> int:
    with sync_session_factory()() as db:
        return int(db.execute(text("SELECT count(*) FROM dw_rubrics")).scalar_one())


def invalid(response: httpx.Response) -> dict[str, Any]:
    assert response.status_code == 422, response.text
    error: dict[str, Any] = response.json()["error"]
    assert error["code"] == "RUBRIC_TEMPLATE_INVALID", error
    assert "{{ and }}" in error["message"]
    return error


async def test_literal_json_braces_are_refused_at_create_naming_the_message(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    rubric = body(("system", JSON_SYSTEM), ("user", "Rate: {text}"))
    error = invalid(await client.post(API, json={"name": "j/json", "body": rubric}))
    assert error["details"]["message_index"] == 0 and error["details"]["role"] == "system"
    assert error["details"]["name"] == '"score"'
    assert error["details"]["allowed_names"] == ["question", "text"]
    assert rubric_rows() == 0
    escaped = JSON_SYSTEM.replace("{", "{{").replace("}", "}}")
    ok = await client.post(
        API, json={"name": "j/json", "body": body(("system", escaped), ("user", "Rate: {text}"))}
    )
    assert ok.status_code == 201, ok.text


async def test_a_name_that_is_not_an_input_field_is_refused(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    error = invalid(
        await client.post(
            API, json={"name": "j/tone", "body": body(("user", "{text} in a {tone} tone"))}
        )
    )
    assert error["details"]["message_index"] == 0 and error["details"]["name"] == "tone"
    assert "{tone} is not an input field" in error["message"]
    unbalanced = invalid(
        await client.post(API, json={"name": "j/brace", "body": body(("user", "Rate {text"))})
    )
    assert unbalanced["details"]["name"] is None and unbalanced["details"]["reason"]
    assert rubric_rows() == 0


async def test_question_and_the_pairwise_names_render(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    single = body(("user", "{question}: {text}"))
    assert (await client.post(API, json={"name": "j/q", "body": single})).status_code == 201
    pairwise = body(
        ("user", "Which is funnier? A: {a} B: {b}\nVERDICT: A or B"),
        style="pairwise",
        input_fields=["left", "right"],
        allowed_verdicts=["A", "B"],
        pair_fields=["left", "right"],
        swap_map={"A": "B", "B": "A"},
    )
    created = await client.post(API, json={"name": "j/pair", "body": pairwise})
    assert created.status_code == 201, created.text


async def test_clone_with_a_body_and_import_are_validated_too(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    good = await client.post(API, json={"name": "j/c", "body": body(("user", "Rate {text}"))})
    assert good.status_code == 201, good.text
    bad = body(("user", "Rate {text}"), ("system", JSON_SYSTEM))
    error = invalid(await client.post(f"{API}/{good.json()['id']}/clone", json={"body": bad}))
    assert error["details"]["message_index"] == 1
    document = (await client.get(f"{API}/{good.json()['id']}/export")).json()
    document.update({"name": "j/imported", "body": bad})
    invalid(await client.post(f"{API}/import", json=document))
    assert rubric_rows() == 1


async def test_a_stored_rubric_that_cannot_render_fails_at_plan_and_start(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    """A rubric saved before the create-time check: the plan names it, nothing is sent."""
    set_operator()
    set_role("judge", model="JEV-9B-decision", protocol="openai_chat")
    stored = body(("system", JSON_SYSTEM), ("user", "Rate: {text}"))
    with sync_session_factory()() as db:
        db.add(
            Rubric(
                id="rb_legacy_bad",
                name="j/legacy",
                version=1,
                content_hash=library_hash("j/legacy", 1, stored),
                body=stored,
                style="pointwise",
                created_by="Test Operator",
                created_by_origin="operator",
            )
        )
        db.commit()
    version_id = make_version(labeling.data_dir, texts(25))
    start = {
        "input_version_id": version_id,
        "role": "judge",
        "rubric_id": "rb_legacy_bad",
        "field_map": {"text": "text"},
        "sampling": {"seed": 11},
    }
    plan = await client.get("/api/v1/label-runs/plan", params={"request": json.dumps(start)})
    error = invalid(plan)
    assert error["details"]["rubric"] == "j/legacy@1"
    assert error["message"].startswith("Rubric j/legacy@1: message 0 (system)")
    started = await client.post("/api/v1/label-runs", json=start)
    invalid(started)
    assert labeling.millm.calls("/v1/chat/completions") == []
    with sync_session_factory()() as db:
        assert db.execute(text("SELECT count(*) FROM dw_label_runs")).scalar_one() == 0
