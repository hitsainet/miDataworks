"""miDataworks stands alone: the regression guard for the operator principle of 2026-10-07.

> "I want miDataworks to have value as a standalone dataset creation tool as well. I want to avoid
> full dependency, but if we can build integrations in that provide additional value when the tools
> are being used in conjunction with each other, I am very supportive of that."

Every test here runs with ``MILLM_BASE_URL`` and ``MISTUDIO_BASE_URL`` both UNSET (the autouse
fixture), and every endpoint role points at :class:`FakeGenericOpenAI` — a server written
independently of the miLLM fake, which answers ``404`` on every ``/api/...`` route and never sends
an ``X-miLLM-*`` header. Three things are pinned:

1. **Core flows succeed**: import, a recipe build with native operators, curation reports, a
   label run (classifier scoring and a judge), calibration, a standard generation run, an export
   and a publish to the (fake) Hub. None of them reaches a sibling.
2. **Integration-only actions refuse clearly**: a ``409`` whose code and details name the missing
   sibling (``mistudio_not_configured`` with ``sibling: miStudio``; ``PROBE_ENDPOINT_UNCONFIGURED``
   naming ``MILLM_BASE_URL``), or, on a generic endpoint, a refusal naming miLLM
   (``BATCH_UNSUPPORTED``, ``STEERING_UNSUPPORTED``). Never a 500, never a hang, and nothing is
   recorded: no send row, no reward mark, no run.
3. **Health** reports both siblings ``configured: false`` while miDataworks is ``ok``.

The audit and its mutation controls: ``0xcc/reviews/standalone_audit_2026-10-07.md``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from src.core.config import get_settings
from src.core.database import sync_session_factory
from tests.support.calibration_fixtures import runner  # noqa: F401 - pytest fixture
from tests.support.fake_generic_openai import EMBED_MODEL, JUDGE_MODEL, MODEL, FakeGenericOpenAI
from tests.support.labeling_fixtures import labeling  # noqa: F401 - pytest fixture
from tests.support.publish_fixtures import publisher  # noqa: F401 - pytest fixture
from tests.support.send_fixtures import sender  # noqa: F401 - pytest fixture
from tests.support.source_fixtures import hf_env  # noqa: F401 - pytest fixture
from tests.support.version_fixtures import driver  # noqa: F401 - pytest fixture

GENERIC = "http://generic.test"
API = "/api/v1"


@pytest.fixture(autouse=True)
def no_siblings(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[httpx.Request]]:
    """Both sibling URLs unset, and any call through the miStudio client recorded as a defect."""
    from src.clients import mistudio_client

    monkeypatch.setattr(get_settings(), "millm_base_url", None)
    monkeypatch.setattr(get_settings(), "mistudio_base_url", None)
    reached: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        reached.append(request)
        return httpx.Response(599, json={"detail": "the standalone guard refuses a miStudio call"})

    monkeypatch.setattr(mistudio_client, "TRANSPORT", httpx.MockTransport(record))
    yield reached
    assert reached == [], f"a standalone flow called miStudio: {[str(r.url) for r in reached]}"


def unset() -> None:
    """Re-unset after a fixture that configures a sibling for its own setup (``sender``)."""
    get_settings().mistudio_base_url = None
    get_settings().millm_base_url = None


def count(model: type) -> int:
    with sync_session_factory()() as db:
        return int(db.scalar(select(func.count()).select_from(model)) or 0)


def refused(response: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    error: dict[str, Any] = response.json()["error"]
    assert error["code"] == code, error
    return error


# --- 3. health ----------------------------------------------------------------------------------


async def test_health_reports_both_siblings_not_configured_while_miDataworks_is_ok(
    client: httpx.AsyncClient,
) -> None:
    body = (await client.get("/api/health")).json()
    assert body["status"] == "ok", body
    for sibling in ("millm", "mistudio"):
        dep = body["dependencies"][sibling]
        assert dep == {"ok": False, "reason": "not configured", "configured": False}, dep


# --- 1. core flows ------------------------------------------------------------------------------


def generic_scoring_template() -> dict[str, Any]:
    """A two-verbalizer scoring template bound to the generic model (any vLLM-style server)."""
    return {
        "kind": "openai_scoring",
        "variant": "completions",
        "render": "Question: {question}\nText: {text}\nAnswer:",
        "input_fields": ["text"],
        "label_set": ["false", "true"],
        "positive_class": "true",
        "decision_kind": "yes_no",
        "verbalizer_ids": [3721, 1802],
        "slots": {"yes_no": [0, 2]},
        "bias": [0.0, 0.0],
        "temperature": {"yes_no": 1.0},
        "bound_model_id": MODEL,
    }


@pytest.fixture
def generic(labeling: Any) -> Iterator[FakeGenericOpenAI]:  # noqa: F811
    """The labeling harness with every origin but the generic server refused."""
    from src.clients import endpoint_caller
    from tests.support.fake_millm import route

    fake = FakeGenericOpenAI()
    previous = endpoint_caller.install_transport(route({GENERIC: fake}))
    try:
        yield fake
    finally:
        endpoint_caller.install_transport(previous)


async def test_a_classifier_and_a_judge_label_run_on_a_generic_server(
    client: httpx.AsyncClient,
    labeling: Any,  # noqa: F811
    generic: FakeGenericOpenAI,
) -> None:
    from tests.support.labeling_fixtures import make_version, set_operator, set_role

    set_operator()
    set_role("classifier", base_url=GENERIC + "/v1", model=MODEL, protocol="openai_scoring")
    set_role("judge", base_url=GENERIC + "/v1", model=JUDGE_MODEL, protocol="openai_chat")
    version_id = make_version(labeling.data_dir, [f"headline {i:03d}" for i in range(12)])
    template = await client.post(
        f"{API}/decision-templates",
        json={"name": "generic/yes-no", "body": generic_scoring_template()},
    )
    assert template.status_code == 201, template.text
    body = {
        "input_version_id": version_id,
        "role": "classifier",
        "template_id": template.json()["id"],
        "question": "Is this funny?",
        "field_map": {"text": "text"},
        "threshold_positive": 0.5,
        "threshold_negative": 0.2,
        "positive_label": "funny",
        "negative_label": "not funny",
    }
    started = await client.post(f"{API}/label-runs", json=body)
    assert started.status_code in (201, 202), started.text
    run = labeling.run_until_done(started.json()["id"])
    assert run.state == "completed", run.error
    assert run.pinned is False, "a generic server offers no lease: the run is unpinned, not refused"
    scores = generic.calls("/v1/completions")
    assert len(scores) == 12 and all(c.body["model"] == MODEL for c in scores)
    rubric = await client.post(
        f"{API}/rubrics",
        json={
            "name": "generic/judge",
            "body": {
                "style": "binary",
                "messages": [
                    {
                        "role": "user",
                        "content": "Is this funny? {text}\nEnd with VERDICT: yes or no.",
                    }
                ],
                "input_fields": ["text"],
                "parser": "verdict_line_v1",
                "allowed_verdicts": ["yes", "no"],
            },
        },
    )
    assert rubric.status_code == 201, rubric.text
    judged = await client.post(
        f"{API}/label-runs",
        json={
            "input_version_id": version_id,
            "role": "judge",
            "rubric_id": rubric.json()["id"],
            "field_map": {"text": "text"},
            "sampling": {"seed": 11},
        },
    )
    assert judged.status_code in (201, 202), judged.text
    final = labeling.run_until_done(judged.json()["id"])
    assert final.state == "completed" and set(final.counts) <= {"yes", "no"}, final.error
    label = (await client.get(f"{API}/label-runs/{final.id}/labels")).json()["items"][0]
    assert label["steering_state"] == "not reported"
    assert generic.miLLM_only_calls() == [], generic.miLLM_only_calls()
    assert labeling.millm.requests == [], "nothing reached the miLLM fake"

    # the batch transport is miLLM's: refused on a generic server, naming miLLM, nothing created
    from src.models.label_run import LabelRun

    before = count(LabelRun)
    error = refused(
        await client.post(f"{API}/label-runs", json={**body, "transport": "batch"}),
        422,
        "BATCH_UNSUPPORTED",
    )
    assert "miLLM" in error["message"]
    assert count(LabelRun) == before


async def test_a_standard_generation_run_on_a_generic_server(
    client: httpx.AsyncClient, gen_generic: Any
) -> None:
    from tests.integration.generation.test_steered_run import dpo_version, steered_body
    from tests.support.generation_fixtures import (
        make_version,
        respond_template,
        run_body,
        version_table,
    )
    from tests.support.generation_fixtures import texts as gen_texts

    gen, fake = gen_generic
    version = make_version(version_table(gen_texts(6)))
    template = await respond_template(client)
    body = run_body(version, template, n_responses=1)
    plan = await client.post(f"{API}/generation-runs/plan", json=body)
    assert plan.status_code == 200, plan.text
    assert plan.json()["server_kind"] == "openai_compatible"
    started = await client.post(f"{API}/generation-runs", json=body)
    assert started.status_code == 202, started.text
    run = gen.run_until_done(started.json()["id"])
    assert run.state == "completed", run.error
    assert run.pinned is False and run.server_kind == "openai_compatible"
    records = (await client.get(f"{API}/generation-runs/{run.id}/records")).json()
    assert records["total"] == 4
    assert fake.miLLM_only_calls() == [], fake.miLLM_only_calls()
    assert len(fake.calls("/v1/chat/completions")) == 4

    # steering is miLLM's: a steered-pair plan and a setting comparison refuse, naming miLLM
    from src.models.generation import GenerationRun

    before = count(GenerationRun)
    profile = {"kind": "profile", "profile_name": "humor"}
    steered = steered_body(dpo_version(gen_texts(4)), template)
    for path in ("/generation-runs/plan", "/generation-runs"):
        error = refused(await client.post(API + path, json=steered), 409, "STEERING_UNSUPPORTED")
        assert "miLLM" in error["message"]
        assert error["details"]["server_kind"] == "openai_compatible"
    # both sides unsteered never reaches a side's own check: the mode's check must still name
    # miLLM rather than fall through to a one-axis refusal (control M12)
    unsteered = {**steered, "setting_a": {"kind": "none"}, "setting_b": {"kind": "none"}}
    error = refused(
        await client.post(f"{API}/generation-runs/plan", json=unsteered),
        409,
        "STEERING_UNSUPPORTED",
    )
    assert error["details"]["server_kind"] == "openai_compatible"
    error = refused(
        await client.post(
            f"{API}/steering-settings/compare",
            json={"setting_a": {"kind": "none"}, "setting_b": profile},
        ),
        409,
        "STEERING_UNSUPPORTED",
    )
    assert "miLLM" in error["message"] and error["details"]["server_kind"] == "openai_compatible"
    assert count(GenerationRun) == before


@pytest.fixture
def gen_generic(
    monkeypatch: pytest.MonkeyPatch, data_dir: Path, clean_db: None
) -> Iterator[tuple[Any, FakeGenericOpenAI]]:
    """007's in-process runner over a REAL loopback generic server (both call paths reach it)."""
    from src.clients import endpoint_caller
    from src.core.celery_app import celery_app
    from src.operators import endpoint_port
    from src.services import labeling_ports
    from src.workers import generation_tasks
    from tests.support.generation_fixtures import FakeMillmServer, Gen, set_operator, set_role

    previous_transport = endpoint_caller.install_transport(None)
    previous_resolver = endpoint_port.resolver()
    previous_leases = endpoint_port.lease_manager()
    labeling_ports.install()
    fake = FakeGenericOpenAI(generation_mode=True)
    with FakeMillmServer(fake) as server:  # type: ignore[arg-type]
        state = Gen(fake, server, data_dir)  # type: ignore[arg-type]
        set_operator()
        set_role("generation", server.base_url + "/v1", MODEL)
        set_role("judge", server.base_url + "/v1", JUDGE_MODEL)
        set_role("embeddings", server.base_url + "/v1", EMBED_MODEL, protocol="openai_embeddings")
        monkeypatch.setattr(celery_app, "send_task", state.send_task)
        monkeypatch.setattr(generation_tasks, "_engine", state.engine)
        monkeypatch.setattr(generation_tasks, "_next_jobs", lambda: None)
        try:
            yield state, fake
        finally:
            endpoint_caller.install_transport(previous_transport)
            endpoint_port.install_resolver(previous_resolver)
            endpoint_port.install_lease_manager(previous_leases)


async def test_an_upload_imports_with_no_sibling(
    client: httpx.AsyncClient,
    hf_env: Any,  # noqa: F811
    operator_name: str,
) -> None:
    import json

    rows = "\n".join(json.dumps({"text": f"a long enough line {i}"}) for i in range(20)).encode()
    response = await client.post(
        f"{API}/sources/uploads",
        files=[("files", ("rows.jsonl", rows, "application/octet-stream"))],
        data={"manifest": json.dumps({"files": [{"name": "rows.jsonl", "split": "train"}]})},
    )
    assert response.status_code == 202, response.text
    [result] = hf_env.run_imports()
    assert hf_env.job(response.json()["job_id"]).status == "completed", result


async def test_a_native_build_curation_export_and_hub_publish_with_no_sibling(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: Any,  # noqa: F811
    publisher: Any,  # noqa: F811
    data_dir: Path,
) -> None:
    from tests.support.publish_fixtures import (
        built_version,
        completed_build,
        publish_and_run,
        store_token,
    )

    await store_token(client)
    version_id = await built_version(client, driver, data_dir)
    for report in ("profile", "leakage"):
        made = await client.post(f"{API}/versions/{version_id}/{report}", json={})
        assert made.status_code in (200, 202), made.text
    build = await completed_build(client, publisher, version_id)
    pub = await publish_and_run(client, publisher, version_id, build["id"])
    assert pub["status"] == "published", pub["error"]


async def test_calibration_computes_with_no_sibling(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    runner: Any,  # noqa: F811
) -> None:
    from tests.support.calibration_fixtures import (
        LABELS,
        MAPPING,
        QUESTION,
        humor_rows,
        humor_scores,
        make_run,
        make_version,
    )

    rows = humor_rows(120)
    version = make_version(data_dir, rows)
    created = await client.post(
        f"{API}/calibration-sets/import",
        json={"version_id": version, "question": QUESTION, "label_set": LABELS, "mapping": MAPPING},
    )
    assert created.status_code == 201, created.text
    run = make_run(version, humor_scores(rows))
    started = await client.post(
        f"{API}/calibration-records",
        json={"label_run_id": run.id, "calibration_set_id": created.json()["id"]},
    )
    assert started.status_code == 202, started.text
    assert runner.run(started.json()["job_id"])["status"] == "completed"


# --- 2. integration-only actions refuse, naming the sibling ------------------------------------


async def test_detector_set_send_refresh_and_reward_mark_refuse_without_miStudio(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    sender: Any,  # noqa: F811
) -> None:
    """A detector set is standalone (built and checked here); its three miStudio actions refuse."""
    from src.models.detector_results import DetectorResults, RewardMark
    from src.models.detector_send import DetectorSend
    from tests.support.send_fixtures import ready_set

    set_id, _, _ = await ready_set(client, sender)
    unset()
    sends, snapshots, marks = count(DetectorSend), count(DetectorResults), count(RewardMark)
    checks = await client.post(f"{API}/detector-sets/{set_id}/checks", json={})
    assert checks.status_code == 200, "a detector set's checks are standalone"
    for response in (
        await client.post(
            f"{API}/detector-sets/{set_id}/send",
            json={"namespace": "someone", "visibility": "private"},
        ),
        await client.post(f"{API}/detector-sets/{set_id}/results/refresh"),
        await client.post(
            f"{API}/reward-marks", json={"mistudio_probe_id": "pm_x", "reason": "trained on it"}
        ),
    ):
        error = refused(response, 409, "mistudio_not_configured")
        assert error["details"]["sibling"] == "miStudio"
        assert error["details"]["setting"] == "MISTUDIO_BASE_URL"
        assert "Hugging Face" in error["message"], "the refusal names the standalone path"
    assert (count(DetectorSend), count(DetectorResults), count(RewardMark)) == (
        sends,
        snapshots,
        marks,
    ), "a refusal records nothing"
    assert sender.mistudio.requests == [], "nothing reached miStudio"


async def test_probe_and_feature_label_runs_refuse_without_miLLM(
    client: httpx.AsyncClient,
    labeling: Any,  # noqa: F811
) -> None:
    from src.models.label_run import LabelRun
    from tests.support.labeling_fixtures import make_version, set_operator

    set_operator()
    version_id = make_version(labeling.data_dir, [f"row {i}" for i in range(5)])
    before = count(LabelRun)
    listed = refused(await client.get(f"{API}/labeling/probes"), 409, "PROBE_ENDPOINT_UNCONFIGURED")
    assert listed["details"] == {"setting": "MILLM_BASE_URL"} and "miLLM" in listed["message"]
    for body in (
        {"role": "probe", "probe": {"probe_id": "pr_x", "window": "all"}},
        {"role": "features", "features": {"top_k": 8, "positions": "last"}},
    ):
        response = await client.post(
            f"{API}/label-runs",
            json={"input_version_id": version_id, "field_map": {"text": "text"}, **body},
        )
        error = refused(response, 409, "PROBE_ENDPOINT_UNCONFIGURED")
        assert "MILLM_BASE_URL" in error["message"]
    assert count(LabelRun) == before
    assert labeling.millm.requests == []


async def test_a_lease_holder_join_on_a_server_that_stopped_being_miLLM_runs_unpinned(
    client: httpx.AsyncClient,
    labeling: Any,  # noqa: F811
) -> None:
    """The holder's own runtime check (``model_lease_holder.join``): a run planned on miLLM whose
    endpoint answers as a generic server by the time it starts degrades to UNPINNED with the
    reason, instead of stopping as "model not loaded" — a lease is a miLLM extra, never a
    precondition. Every caller gates on the plan's server kind, so only this case reaches it
    (the standalone audit's control M9 survived the whole suite until this test)."""
    from tests.integration.labeling.test_label_run_worker import start

    run = await start(client, labeling, 6)
    original = labeling.millm.handle

    def no_longer_millm(request: Any) -> Any:
        if request.url.path.startswith("/api/"):
            return httpx.Response(404, json={"detail": "Not Found"})
        return original(request)

    labeling.millm.handle = no_longer_millm  # type: ignore[method-assign]
    final = labeling.run_until_done(run["id"])
    assert final.state == "completed", final.error
    assert final.pinned is False
    assert labeling.millm.scoring_calls == 6, "every row was still scored"
