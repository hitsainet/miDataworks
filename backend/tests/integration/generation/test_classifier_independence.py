"""Classifier independence (017 FTASKS 9.9; FR-017.42; FTID §3.8).

``POST /api/v1/generation-runs/independence-check`` with ``role = classifier`` compares the
classifier endpoint with the generators the named runs RECORDED. A classifier serving one of those
models is flagged; a different model reads independent; a classifier that does not disclose its
model (TypeSafe's hosted alias) reads "not reported" and ``independent`` None, never True.
Control M20 (the classifier branch removed) turns these red.
"""

from __future__ import annotations

from typing import Any

import httpx

from src.core.database import sync_session_factory
from src.core.ids import new_id
from src.models.generation import GenerationRun
from tests.support import db_factories
from tests.support.labeling_fixtures import Labeling, labeling, set_operator, set_role
from tests.support.second_opinion import Paid, paid

__all__ = ["labeling", "paid"]

CHECK = "/api/v1/generation-runs/independence-check"
#: The fake miLLM's resident classifier model and revision (``tests/support/fake_millm.py``).
CLASSIFIER = {
    "model_id": "JEV-9B-decision",
    "revision": "b63f651ce8ed64481d3f5e73ecdb05f740042f01",
    "set_hash": "none",
}


def generation_run(*identities: dict[str, Any]) -> str:
    with sync_session_factory()() as db:
        version = db_factories.version(db)
        db.commit()
        run = GenerationRun(
            id=new_id("gr"),
            mode="standard",
            target_type="sft",
            input_version_id=str(version.id),
            held_out_origin_version_id=str(version.id),
            held_out_splits=["test"],
            prompt_column="text",
            seed_splits=["train"],
            sample_size=10,
            seed=1,
            n_responses=1,
            stages=["respond"],
            generation_endpoint={"role": "generation"},
            server_kind="millm",
            generator_identities=list(identities),
            engine_path="native",
            steering_supported=False,
            state="completed",
            warnings=[],
            counts={},
            started_by="Test Operator",
            started_by_origin="operator",
        )
        db.add(run)
        db.commit()
        return run.id


async def check(client: httpx.AsyncClient, *run_ids: str) -> dict[str, Any]:
    response = await client.post(
        CHECK, json={"role": "classifier", "generator_run_ids": list(run_ids)}
    )
    assert response.status_code == 200, response.text
    out: dict[str, Any] = response.json()
    return out


async def test_a_classifier_serving_a_generators_model_is_flagged(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    set_operator()
    set_role("classifier")
    other = generation_run({"model_id": "gen-model-x", "revision": "r1", "set_hash": "none"})
    same = generation_run(CLASSIFIER)
    flagged = await check(client, other, same)
    assert flagged["role"] == "classifier" and flagged["disclosed"] is True
    assert flagged["classifier_identity"] == CLASSIFIER
    assert flagged["independent"] is False
    assert flagged["conflicts"] == [{"classifier": CLASSIFIER, "generator": CLASSIFIER}]
    clear = await check(client, other)
    assert clear["independent"] is True and clear["conflicts"] == []


async def test_an_undisclosed_classifier_reads_not_reported_never_independent(
    client: httpx.AsyncClient, paid: Paid
) -> None:
    run = generation_run({"model_id": "gen-model-x", "revision": "r1", "set_hash": "none"})
    answer = await check(client, run)
    assert answer["independent"] is None and answer["disclosed"] is False
    assert answer["classifier_identity"]["model_id"] == "not reported"
    assert "not reported" in answer["message"]
    assert paid.scoring() == [], "nothing was sent to the paid endpoint to find out"


async def test_the_classifier_role_needs_the_generation_runs(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    set_role("classifier")
    response = await client.post(CHECK, json={"role": "classifier"})
    assert response.status_code == 422
    assert response.json()["error"]["details"]["next"]["tool"] == "dataworks_list_generation_runs"
    missing = await client.post(CHECK, json={"role": "classifier", "generator_run_ids": ["gr_x"]})
    assert missing.status_code == 404
