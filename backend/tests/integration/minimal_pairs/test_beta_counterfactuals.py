"""Counterfactuals made by the chain for a behaviour code cannot edit (017 FTASKS 8.3; FR-017.35).

fixture-beta's carriers are written whole by an instructed generator: there is no slot, so
``slot_siblings@1`` makes nothing of them (``tests/unit/operators/test_slot_siblings.py``). Their
counterfactuals go through 009's minimal-pair chain: 007's generation run edits each carrier, the
scope build pairs it with its seed, 005's judge reads both through a pinned rubric, and the pair
build keeps verified flips only, recording the judge on every row. Every stage is the real piece
(the chain's own conftest); the judge answers from the row text alone.
"""

from __future__ import annotations

from typing import Any

import httpx

from tests.integration.minimal_pairs.test_minimal_pair_chain import (
    CHAINS,
    advance,
    chain_get,
    detector_table,
    judge_job,
    load_judge,
    rows_of,
    template_id,
    version_of,
)
from tests.support.generation_fixtures import JUDGE_MODEL, Gen, make_version
from tests.support.label_fixtures import BETA_TEXTS
from tests.support.operator_build_driver import RealDriver

ELEMENT = "quorbish"
#: fixture-beta carriers that complied (the element written) and the generator's one-word edit
#: that takes it out; the last one keeps it (the judge must catch an unverified flip).
SEEDS = {
    f"{BETA_TEXTS['complied_yes']} [{framing}]": edit
    for framing, edit in (
        ("direct", f"{BETA_TEXTS['complied_yes']} [direct]".replace(ELEMENT, "subject")),
        ("hedged", f"{BETA_TEXTS['complied_yes']} [hedged]".replace(ELEMENT, "matter")),
        ("quoted", f"{BETA_TEXTS['complied_yes']} [quoted]".replace("asked", "wanted")),
    )
}
RUBRIC = {
    "style": "binary",
    "messages": [
        {
            "role": "user",
            "content": "Does ROW describe the requested topic by name?\nROW: {text}\nEND\n"
            "End with VERDICT: yes or no.",
        }
    ],
    "input_fields": ["text"],
    "parser": "verdict_line_v1",
    "allowed_verdicts": ["yes", "no"],
}


def answer(messages: list[dict[str, Any]], body: dict[str, Any]) -> str:
    content = str(messages[-1]["content"])
    if "ROW: " in content:
        row = content.split("ROW: ", 1)[1].split("\nEND", 1)[0]
        return f"VERDICT: {'yes' if ELEMENT in row else 'no'}"
    return SEEDS[content.split("Text:\n", 1)[1]]


async def test_a_fixture_beta_counterfactual_goes_through_the_chain_and_records_the_judge(
    client: httpx.AsyncClient, gen: Gen, chain_env: RealDriver
) -> None:
    gen.fake.gen_answer = answer
    seeds = make_version(
        detector_table(list(SEEDS)),
        target_type="detector",
        roles={"text": "content", "label": "metadata"},
        held_out=True,
    )
    rubric = await client.post(
        "/api/v1/rubrics", json={"name": "fixture-beta/complied", "body": RUBRIC}
    )
    assert rubric.status_code == 201, rubric.text
    started = await client.post(
        CHAINS,
        json={
            "input_version_id": seeds,
            "text_column": "text",
            "seed_splits": ["train"],
            "sample_size": 10,
            "seed": 3,
            "respond_template_id": await template_id(client),
            "rubric_id": rubric.json()["id"],
            "flip_from": "yes",
            "flip_to": "no",
            "judge_seed": 1,
            "max_edit_words": 2,
        },
    )
    assert started.status_code == 202, started.text
    chain = started.json()
    assert gen.run_until_done(chain["stages"][0]["run_id"]).state == "completed"
    moved = await advance(chain["id"])
    assert chain_env.run(moved.scope_job_id) == "completed"
    load_judge(gen)
    judging = await advance(chain["id"])
    assert judging.stage == "judge" and judging.judge_run_id is not None
    from src.workers import label_run_tasks

    label_run_tasks.run_job(judge_job(judging.judge_run_id))
    waiting = await advance(chain["id"])
    assert chain_env.run(waiting.pair_job_id) == "completed"
    done = await advance(chain["id"])
    assert done.state == "completed", done.error
    out = await chain_get(client, chain["id"])
    assert out["pair_version_id"] == version_of(waiting.pair_job_id)
    assert out["counts"]["pairs_judged"] == 3
    assert out["counts"]["pairs_verified"] == 2
    assert out["counts"]["unverified_by_reason"] == {"flip_not_verified": 1}
    rows = rows_of(out["pair_version_id"])
    assert len(rows) == 4
    for row in rows:  # the judge is recorded on every kept row
        assert row["pair_judge_model_id"] == JUDGE_MODEL
        assert row["pair_judge_run_id"] == judging.judge_run_id
        assert row["pair_judge_rubric"] == "fixture-beta/complied@1"
        assert row["pair_judge_verdict"] == ("yes" if row["pair_role"] == "seed" else "no")
    assert {r["text"] for r in rows if r["pair_role"] == "counterpart"} == {
        SEEDS[s] for s in SEEDS if "[quoted]" not in s
    }
