"""Judge runs, the parse-failure guard, re-derive and aggregate (005 FTASKS 11.x)."""

from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy import select

from src.core.database import sync_session_factory
from src.models.label import Label
from tests.integration.labeling.helpers import setup_classifier, start_body, texts
from tests.support.labeling_fixtures import Labeling, make_version, set_operator, set_role

RUBRIC = {
    "style": "binary",
    "messages": [
        {"role": "user", "content": "Is this funny? {text}\nEnd with VERDICT: yes or no."}
    ],
    "input_fields": ["text"],
    "parser": "verdict_line_v1",
    "allowed_verdicts": ["yes", "no"],
}


async def judge_setup(
    client: httpx.AsyncClient, lab: Labeling, n: int, rubric: dict[str, Any] = RUBRIC
) -> tuple[str, str]:
    set_operator()
    set_role("judge", model="JEV-9B-decision", protocol="openai_chat")
    created = (await client.post("/api/v1/rubrics", json={"name": f"j/{n}", "body": rubric})).json()
    return make_version(lab.data_dir, texts(n)), created["id"]


def judge_body(version_id: str, rubric_id: str, **kw: Any) -> dict[str, Any]:
    return {
        "input_version_id": version_id,
        "role": "judge",
        "rubric_id": rubric_id,
        "field_map": {"text": "text"},
        "sampling": {"seed": 11},
        **kw,
    }


async def test_a_judge_run_records_verdicts_rationale_and_seed(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    version_id, rubric_id = await judge_setup(client, labeling, 20)
    labeling.millm.judge_answer = lambda msgs: "Short reason.\nVERDICT: " + (
        "yes" if "0" in msgs[0]["content"][-30:] else "no"
    )
    run = (await client.post("/api/v1/label-runs", json=judge_body(version_id, rubric_id))).json()
    assert run["kind"] == "judge" and run["structured_output"] == "strict_parse"
    final = labeling.run_until_done(run["id"])
    assert final.state == "completed" and set(final.counts) <= {"yes", "no"}
    label = (await client.get(f"/api/v1/label-runs/{run['id']}/labels")).json()["items"][0]
    assert (
        label["rationale"] == "Short reason."
        and label["parsed_value"]["seed"] == '11;scope="request"'
    )
    assert label["steering_state"] == "not reported" and label["probability"] is None
    sent = labeling.millm.calls("/v1/chat/completions")[0]
    assert sent.body["temperature"] == 0.0 and sent.body["seed"] == 11
    assert sent.headers["x-millm-load-policy"] == "refuse"


async def test_parse_failures_above_the_share_stop_the_run_keeping_rows(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    version_id, rubric_id = await judge_setup(client, labeling, 450)
    labeling.millm.judge_answer = lambda msgs: "I cannot decide."
    final_run = (
        await client.post("/api/v1/label-runs", json=judge_body(version_id, rubric_id))
    ).json()
    final = labeling.run_until_done(final_run["id"])
    assert final.state == "failed" and final.error["code"] == "PARSE_FAILURES"
    with sync_session_factory()() as db:
        rows = (
            db.execute(select(Label).where(Label.label_run_id == final_run["id"])).scalars().all()
        )
    assert len(rows) == 200 and {r.outcome for r in rows} == {"parse_failure"}
    assert all(r.raw_output["content"] == "I cannot decide." for r in rows)


async def test_structured_output_downgrades_when_refused(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    schema = {
        "type": "object",
        "required": ["verdict"],
        "properties": {"verdict": {"type": "string"}},
    }
    rubric = {**RUBRIC, "parser": "json_v1", "json_schema": schema}
    version_id, rubric_id = await judge_setup(client, labeling, 5, rubric)
    labeling.millm.honour_response_format = False
    labeling.millm.judge_answer = lambda msgs: '{"verdict": "yes"}'
    run = (await client.post("/api/v1/label-runs", json=judge_body(version_id, rubric_id))).json()
    assert run["structured_output"] == "json_schema"
    final = labeling.run_until_done(run["id"])
    assert (
        final.structured_output == "strict_parse"
        and final.labeler_fingerprint != run["labeler_fingerprint"]
    )
    assert final.counts == {"yes": 5}


async def test_rederive_makes_no_call_and_leaves_the_parent(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    version_id, template_id = await setup_classifier(client, labeling, n=50)
    parent = (
        await client.post("/api/v1/label-runs", json=start_body(version_id, template_id))
    ).json()
    labeling.run_until_done(parent["id"])
    calls = len(labeling.millm.requests)
    response = await client.post(
        f"/api/v1/label-runs/{parent['id']}/rederive",
        json={"threshold_positive": 0.9, "threshold_negative": 0.1},
    )
    assert response.status_code == 201, response.text
    child = response.json()
    assert child["parent_run_ids"] == [parent["id"]] and child["kind"] == "rederived"
    final = labeling.run_until_done(child["id"])
    assert final.state == "completed"
    assert len(labeling.millm.requests) == calls  # NO endpoint call
    with sync_session_factory()() as db:
        child_rows = {
            r.row_key: r
            for r in db.execute(select(Label).where(Label.label_run_id == child["id"])).scalars()
        }
        parent_rows = {
            r.row_key: r
            for r in db.execute(select(Label).where(Label.label_run_id == parent["id"])).scalars()
        }
    assert len(child_rows) == 50
    for key, row in child_rows.items():
        p = parent_rows[key].probability
        assert row.probability == p and row.raw_output is None
        assert row.outcome == ("positive" if p >= 0.9 else "negative" if p <= 0.1 else "excluded")
        expected_parent = "positive" if p >= 0.5 else "negative" if p <= 0.2 else "excluded"
        assert parent_rows[key].outcome == expected_parent  # unchanged
    bad = await client.post(
        f"/api/v1/label-runs/{parent['id']}/rederive",
        json={"threshold_positive": 0.1, "threshold_negative": 0.9},
    )
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "THRESHOLDS_INVALID"


async def test_aggregate_flags_disagreement(client: httpx.AsyncClient, labeling: Labeling) -> None:
    version_id, rubric_id = await judge_setup(client, labeling, 6)
    run_ids = []
    for verdicts in (["yes"] * 6, ["yes"] * 3 + ["no"] * 3, ["no"] * 6):
        answers = iter(verdicts)
        labeling.millm.judge_answer = lambda msgs, a=answers: "r\nVERDICT: " + next(a)
        run = (
            await client.post(
                "/api/v1/label-runs",
                json=judge_body(version_id, rubric_id, question=str(len(run_ids))),
            )
        ).json()
        labeling.run_until_done(run["id"])
        run_ids.append(run["id"])
    response = await client.post("/api/v1/label-runs/aggregate", json={"run_ids": run_ids})
    assert response.status_code == 201, response.text
    agg = labeling.run_until_done(response.json()["id"])
    assert agg.state == "completed"
    with sync_session_factory()() as db:
        rows = (
            db.execute(select(Label).where(Label.label_run_id == agg.id).order_by(Label.row_key))
            .scalars()
            .all()
        )
    flags = {r.parsed_value["flag"] for r in rows}
    assert flags == {"disagreement"} and len(rows) == 6
    assert all(set(r.parsed_value["verdicts"]) == set(run_ids) for r in rows)
    two = await client.post("/api/v1/label-runs/aggregate", json={"run_ids": run_ids[:2]})
    agg2 = labeling.run_until_done(two.json()["id"])
    with sync_session_factory()() as db:
        rows2 = db.execute(select(Label).where(Label.label_run_id == agg2.id)).scalars().all()
    by_outcome = {r.outcome for r in rows2}
    assert "excluded" in by_outcome  # yes/no ties
    dup = await client.post(
        "/api/v1/label-runs/aggregate", json={"run_ids": [run_ids[0], run_ids[0]]}
    )
    assert dup.status_code == 422 and dup.json()["error"]["code"] == "AGGREGATE_DUPLICATE"
