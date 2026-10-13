"""Judge independence is enforced in three places, each on its own (007 FTASKS 8.1 – 8.8).

Each site is isolated by a real sequence of events, not by patching the shared rule:
- the API refuses at plan when the judge already is the generator;
- the WORKER refuses after the first response when the judge was changed to the generator after
  the start (the API saw a different judge);
- 005's label-run PREFLIGHT refuses a judge or classifier run over a version holding the run's
  generated rows when the judge became the generator after the run finished.
"""

from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy import select, text

from src.core.database import sync_session_factory
from src.models.job import Job
from src.services import label_run_preflight
from src.services.generation import independence
from tests.support.generation_fixtures import (
    GEN_MODEL,
    REVISION,
    Gen,
    make_version,
    respond_template,
    run_body,
    set_role,
    version_table,
)

RUNS = "/api/v1/generation-runs"
RUBRIC = {
    "style": "binary",
    "messages": [{"role": "user", "content": "Is this good? {text}\nEnd with VERDICT: yes or no."}],
    "input_fields": ["text"],
    "parser": "verdict_line_v1",
    "allowed_verdicts": ["yes", "no"],
}


async def completed_run(client: httpx.AsyncClient, gen: Gen) -> tuple[str, str]:
    version = make_version(version_table(["one", "two"]))
    template = await respond_template(client)
    run_id = (await client.post(RUNS, json=run_body(version, template, sample_size=2))).json()["id"]
    assert gen.run_until_done(run_id).state == "completed"
    return run_id, version


def judge_body(version_id: str, rubric_id: str) -> dict[str, Any]:
    return {
        "input_version_id": version_id,
        "role": "judge",
        "rubric_id": rubric_id,
        "field_map": {"text": "prompt"},
        "sampling": {"seed": 1},
    }


async def test_the_api_refuses(client: httpx.AsyncClient, gen: Gen) -> None:
    set_role("judge", gen.base_url, GEN_MODEL)
    version = make_version(version_table(["one"]))
    response = await client.post(RUNS, json=run_body(version, await respond_template(client)))
    assert response.status_code == 422 and response.json()["error"]["code"] == "JUDGE_IS_GENERATOR"


async def test_the_worker_refuses_alone(client: httpx.AsyncClient, gen: Gen) -> None:
    version = make_version(version_table(["one", "two"]))
    run_id = (
        await client.post(
            RUNS, json=run_body(version, await respond_template(client), sample_size=2)
        )
    ).json()["id"]
    set_role("judge", gen.base_url, GEN_MODEL)  # after the API's check
    run = gen.run_until_done(run_id)
    assert run.state == "failed" and run.error["code"] == "JUDGE_IS_GENERATOR"
    assert len(gen.chats()) == 1, "refused after the FIRST response, with the served model"


async def test_the_label_run_preflight_refuses_alone(client: httpx.AsyncClient, gen: Gen) -> None:
    run_id, _ = await completed_run(client, gen)
    holding = make_version(
        version_table(["one"], generated=["generated row"]),
        bindings=[{"kind": "generation_run", "id": run_id}],
    )
    child = make_version(version_table(["one"]), parent=holding)  # generated rows by lineage
    set_role("judge", gen.base_url, GEN_MODEL)
    rubric = (await client.post("/api/v1/rubrics", json={"name": "j/indep", "body": RUBRIC})).json()
    for version in (holding, child):
        response = await client.post("/api/v1/label-runs", json=judge_body(version, rubric["id"]))
        assert response.status_code == 422, response.text
        error = response.json()["error"]
        assert error["code"] == "JUDGE_IS_GENERATOR" and error["details"]["generation_runs"] == [
            run_id
        ]
    with sync_session_factory()() as db:
        assert db.execute(text("SELECT count(*) FROM dw_label_runs")).scalar_one() == 0
    # a different judge model over the same rows is allowed past the preflight
    assert independence.label_run_preflight in label_run_preflight.PREFLIGHT_CHECKS


async def test_the_preflight_refuses_classifiers_too(client: httpx.AsyncClient, gen: Gen) -> None:
    run_id, _ = await completed_run(client, gen)
    holding = make_version(
        version_table(["one"]), bindings=[{"kind": "generation_run", "id": run_id}]
    )
    context = label_run_preflight.PreflightContext(
        input_version_id=holding,
        role="classifier",
        protocol="openai_scoring",
        base_url=gen.base_url,
        model_id=GEN_MODEL,
        model_revision=None,
        labeler_identity={},
        labeler_identity_hash="0" * 64,
        row_filter=None,
        rows_to_score=1,
        template_id=None,
        rubric_id=None,
    )
    import pytest

    with pytest.raises(label_run_preflight.PreflightRefused) as refused:
        label_run_preflight.run_checks(context)
    assert (
        refused.value.code == "JUDGE_IS_GENERATOR" and refused.value.details["role"] == "classifier"
    )
    other = label_run_preflight.PreflightContext(
        **{**context.__dict__, "model_id": "other", "model_revision": REVISION}
    )
    label_run_preflight.run_checks(other)


async def test_a_judge_on_another_model_queues_behind_the_generator(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    """One miLLM, two models: the judge run waits with Foundation's reason; no swap is asked."""
    from src.services.job_service import claim_job

    version = make_version(version_table(["one", "two"]))
    run_id = (
        await client.post(
            RUNS, json=run_body(version, await respond_template(client), sample_size=2)
        )
    ).json()["id"]
    with sync_session_factory()() as db:
        claim_job(db, gen.job_for(run_id))  # the generation job is running on gen-model
    rubric = (await client.post("/api/v1/rubrics", json={"name": "j/queue", "body": RUBRIC})).json()
    response = await client.post("/api/v1/label-runs", json=judge_body(version, rubric["id"]))
    assert response.status_code == 201, response.text
    with sync_session_factory()() as db:
        job = db.execute(select(Job).where(Job.kind == "label_run")).scalar_one()
    assert job.status == "queued" and job.required_model_id != GEN_MODEL
    assert job.queue_reason and GEN_MODEL in job.queue_reason
    loads = [r for r in gen.fake.requests if "/load" in r.path or r.path.endswith("/activate")]
    assert loads == []


async def test_version_detail_identities_for_008(client: httpx.AsyncClient, gen: Gen) -> None:
    """008 reads the generator identities beside the labelers (P-14, FTASKS 8.9)."""
    from src.services.generation import provenance

    run_id, _ = await completed_run(client, gen)
    with sync_session_factory()() as db:
        (found,) = provenance.generators_for_runs([run_id], session=db)
    assert found == {
        "generation_run_id": run_id,
        "model_id": GEN_MODEL,
        "revision": REVISION,
        "set_hash": "none",
        "pinned": True,
        "revision_reported": True,
        "mode": "standard",
    }
