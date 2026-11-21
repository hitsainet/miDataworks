"""Disagreements go to review (017 FTASKS 9.11; FR-017.44; FTID §3.8, I17).

``POST /api/v1/label-runs/handoffs/{handoff_id}/review`` over a second-opinion run whose labels
are written here (the run's outcomes against the version's ``truth`` column, disagreeing on rows
the test chose): the queues hold EXACTLY the disagreeing keys; 12,000 disagreements open three
queues with every genre in the first; over ``REVIEW_MAX_QUEUES_PER_HANDOFF`` is refused and
writes nothing; an operator override reaches the effective label and an agent decision does not.
Control M22 (the hand-off call removed) turns these red.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import func, insert, select

from src.core.config import get_settings
from src.core.database import sync_session_factory
from src.core.ids import new_id
from src.models.label import Label
from src.models.label_run import LabelRun
from src.models.review import ReviewItem, ReviewQueue
from src.services.calibration.mapping import question_hash
from src.services.review import effective_label
from tests.support.labeling_fixtures import make_version, row_key

AGENT = {"X-Dataworks-Agent": "agent:dataworks-mcp"}
GENRES = ("ga", "gb", "gc", "gd", "ge")
QUESTION = "Does the text show the behaviour?"


def labelled_run(
    data_dir: Path, n: int, disagree: set[int], *, handoff: str = "ho-1", genre_of: Any = None
) -> tuple[str, str, list[str]]:
    """A version of ``n`` rows and a completed run over it whose outcome disagrees with
    ``truth`` exactly on the indices in ``disagree``. Returns (version, run, row keys)."""
    texts = [f"row {i:06d}" for i in range(n)]
    truth = [i % 2 == 0 for i in range(n)]
    genres = [genre_of(i) if genre_of else GENRES[i % len(GENRES)] for i in range(n)]
    v = make_version(data_dir, texts, extra={"truth": truth, "genre": genres})
    run_id = new_id("lr")
    keys = [row_key(t) for t in texts]
    with sync_session_factory()() as db:
        db.add(
            LabelRun(
                id=run_id,
                kind="classifier",
                state="completed",
                input_version_id=v,
                chunk_size=1000,
                question=QUESTION,
                positive_label="yes",
                negative_label="no",
                label_set=["yes", "no"],
                labeler_identity={"kind": "classifier"},
                labeler_identity_hash="b" * 64,
                labeler_fingerprint="c" * 64,
                started_by="Test Operator",
                started_by_origin="operator",
                target_column="truth",
                handoff_id=handoff,
                behaviour_key="fixture-alpha",
                rows_total=n,
            )
        )
        db.flush()
        db.execute(
            insert(Label),
            [
                {
                    "label_run_id": run_id,
                    "row_key": keys[i],
                    "labeler_fingerprint": "c" * 64,
                    "outcome": "positive" if (truth[i] != (i in disagree)) else "negative",
                    "probability": 0.9 if (truth[i] != (i in disagree)) else 0.1,
                    "steering_state": "not reported",
                    "parsed_value": {"choice": "test"},
                    "raw_output": {"test": True},
                }
                for i in range(n)
            ],
        )
        db.commit()
    return v, run_id, keys


def queue_keys(queue_id: str) -> list[str]:
    with sync_session_factory()() as db:
        return list(
            db.execute(
                select(ReviewItem.row_key)
                .where(ReviewItem.queue_id == queue_id)
                .order_by(ReviewItem.position)
            ).scalars()
        )


async def test_the_queues_hold_exactly_the_disagreeing_rows(
    client: httpx.AsyncClient, data_dir: Path, operator_name: str
) -> None:
    rng = random.Random(4)
    disagree = set(rng.sample(range(300), 37))
    _, run_id, keys = labelled_run(data_dir, 300, disagree)
    response = await client.post("/api/v1/label-runs/handoffs/ho-1/review", headers=AGENT)
    assert response.status_code == 201, response.text
    out = response.json()
    assert out["disagreements"] == 37 and out["queues"] == 1
    (run,) = out["runs"]
    assert run["label_run_id"] == run_id and run["target_column"] == "truth"
    (queue,) = run["queues"]
    assert set(queue_keys(queue["queue_id"])) == {keys[i] for i in disagree}
    with sync_session_factory()() as db:
        stored = db.get(ReviewQueue, queue["queue_id"])
        assert stored is not None
        assert (stored.handoff_id, stored.kind, stored.label_run_id) == (
            "ho-1",
            "label_review",
            run_id,
        )
        assert stored.behaviour_key == "fixture-alpha"
        assert stored.created_by_origin == "agent"


async def test_twelve_thousand_disagreements_open_three_queues_every_genre_in_the_first(
    client: httpx.AsyncClient, data_dir: Path, operator_name: str
) -> None:
    n = 12_500
    # genre ge is rare: its 20 rows (all disagreeing) are the version's last rows
    genre_of = lambda i: "ge" if i >= n - 20 else GENRES[i % 4]  # noqa: E731
    disagree = set(range(11_980)) | set(range(n - 20, n))
    _, _, keys = labelled_run(data_dir, n, disagree, genre_of=genre_of)
    response = await client.post("/api/v1/label-runs/handoffs/ho-1/review", json={"by": ["genre"]})
    assert response.status_code == 201, response.text
    out = response.json()
    assert out["disagreements"] == len(disagree) == 12_000
    (run,) = out["runs"]
    sizes = [q["rows"] for q in run["queues"]]
    assert sizes == [5000, 5000, 2000]
    first = set(queue_keys(run["queues"][0]["queue_id"]))
    genre_by_key = {keys[i]: genre_of(i) for i in range(n)}
    assert {genre_by_key[k] for k in first} == set(GENRES)
    every = [k for q in run["queues"] for k in queue_keys(q["queue_id"])]
    assert len(every) == len(set(every)) == 12_000
    assert set(every) == {keys[i] for i in disagree}


async def test_more_queues_than_the_cap_is_refused_and_writes_nothing(
    client: httpx.AsyncClient,
    data_dir: Path,
    operator_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "review_max_queues_per_handoff", 2)
    labelled_run(data_dir, 12_000, set(range(11_000)))
    response = await client.post("/api/v1/label-runs/handoffs/ho-1/review")
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "cap_exceeded"
    assert error["details"]["setting"] == "REVIEW_MAX_QUEUES_PER_HANDOFF"
    assert (error["details"]["limit"], error["details"]["actual"]) == (2, 3)
    with sync_session_factory()() as db:
        assert db.execute(select(func.count()).select_from(ReviewQueue)).scalar_one() == 0
    missing = await client.post("/api/v1/label-runs/handoffs/ho-none/review")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "handoff_not_found"


async def test_an_operator_override_changes_the_label_and_an_agent_decision_does_not(
    client: httpx.AsyncClient, data_dir: Path, operator_name: str
) -> None:
    v, run_id, keys = labelled_run(data_dir, 20, {0, 3})
    out = (await client.post("/api/v1/label-runs/handoffs/ho-1/review", headers=AGENT)).json()
    queue_id = out["runs"][0]["queues"][0]["queue_id"]
    items = (await client.get(f"/api/v1/review-queues/{queue_id}/items")).json()["items"]
    by_key = {i["row_key"]: i["id"] for i in items}
    assert set(by_key) == {keys[0], keys[3]}
    # row 0: truth positive, run said no. The agent cannot override; its accept changes nothing.
    agent = await client.post(
        f"/api/v1/review-items/{by_key[keys[0]]}/decisions",
        json={"decision": "override", "override_label": "yes", "reason": "agent thinks so"},
        headers=AGENT,
    )
    assert agent.status_code == 403, agent.text
    accepted = await client.post(
        f"/api/v1/review-items/{by_key[keys[0]]}/decisions",
        json={"decision": "accept", "reason": "agent"},
        headers=AGENT,
    )
    assert accepted.status_code == 201, accepted.text
    operator = await client.post(
        f"/api/v1/review-items/{by_key[keys[3]]}/decisions",
        json={"decision": "override", "override_label": "no", "reason": "operator checked"},
    )
    assert operator.status_code == 201, operator.text
    states = effective_label.resolve(v, question_hash(QUESTION), run_id, [keys[0], keys[3]])
    assert states[keys[0]].state == "model" and states[keys[0]].label == "no"
    assert states[keys[3]].state == "overridden" and states[keys[3]].label == "no"


def test_interleave_takes_one_key_of_every_stratum_in_turn() -> None:
    from src.services.review.handoff import interleave

    assert interleave({"b": ["b1", "b2", "b3"], "a": ["a1"], "c": ["c1", "c2"]}) == [
        "a1", "b1", "c1", "b2", "c2", "b3",
    ]  # fmt: skip
    assert interleave({}) == []
