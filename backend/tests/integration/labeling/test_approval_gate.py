"""P-07 at REST (C6) and the start's refusals (005 FTASKS 8.2 – 8.8, 18.1, 18.2; criteria 9, 10).

The ledger is the SAME one recipe builds draw on (FR-002.50, S3-02): a build's labelling rows and a
label run's rows on one version share one 24 h budget.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from src.core.database import sync_session_factory
from src.models.agent_label_row import AgentLabelRow
from src.models.approval import Approval
from src.models.job import Job
from src.models.label_run import LabelRun
from src.services import label_run_preflight
from tests.integration.labeling.helpers import (
    AGENT,
    jev_template_id,
    setup_classifier,
    start_body,
    texts,
)
from tests.support.labeling_fixtures import Labeling, make_version, set_operator, set_role


def count(model: Any) -> int:
    """Rows of ``model``; for jobs, label jobs only (the version factory adds a build job)."""
    with sync_session_factory()() as db:
        query = select(func.count()).select_from(model)
        if model is Job:
            query = query.where(Job.kind.like("label%"))
        return int(db.execute(query).scalar_one())


async def post(
    client: httpx.AsyncClient, body: dict[str, Any], agent: bool = True
) -> httpx.Response:
    return await client.post("/api/v1/label-runs", json=body, headers=AGENT if agent else {})


async def test_agent_5001_rows_waits_and_5000_starts(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    version_big, template_id = await setup_classifier(client, labeling, n=5001)
    gated = await post(client, start_body(version_big, template_id))
    assert gated.status_code == 202, gated.text
    assert gated.json()["action"] == "agent_label_rows"
    assert count(LabelRun) == 0 and count(Job) == 0
    version_ok = make_version(labeling.data_dir, texts(5000, "other"))
    started = await post(client, start_body(version_ok, template_id))
    assert started.status_code == 201, started.text
    run = started.json()
    assert run["started_by"] == "agent:claude" and run["started_by_origin"] == "agent"
    assert run["agent_counted_rows"] == 5000
    with sync_session_factory()() as db:
        (ledger,) = db.execute(select(AgentLabelRow)).scalars().all()
    assert (ledger.version_id, ledger.rows_counted, ledger.run_kind) == (
        version_ok,
        5000,
        "label_run",
    )


async def test_two_agent_runs_on_one_version_sum_in_the_window(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    _, template_id = await setup_classifier(client, labeling, n=1)
    # 3,000 rows each, by filtering two halves of one version
    v = make_version(
        labeling.data_dir,
        texts(3000, "g") + texts(3000, "s"),
        ["generated"] * 3000 + ["source"] * 3000,
    )
    first = await post(client, start_body(v, template_id, row_filter={"origin": "generated"}))
    assert first.status_code == 201, first.text
    second = await post(client, start_body(v, template_id, row_filter={"origin": "source"}))
    assert second.status_code == 202
    # the same on two versions is not gated
    other = make_version(labeling.data_dir, texts(3000, "x"))
    assert (await post(client, start_body(other, template_id))).status_code == 201


async def test_a_build_and_a_label_run_share_the_budget(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    """FR-002.50 (S3-02): rows a recipe build admitted for labelling count against the same
    version's window as a label run."""
    from src.core.database import async_session_factory
    from src.services import agent_label_ledger

    version_id, template_id = await setup_classifier(client, labeling, n=3000)
    async with async_session_factory()() as db:
        await agent_label_ledger.admit(db, version_id, "agent:claude", "label_run", "build-1", 3000)
        await db.commit()
    assert (await post(client, start_body(version_id, template_id))).status_code == 202


async def test_reused_rows_do_not_count(client: httpx.AsyncClient, labeling: Labeling) -> None:
    version_id, template_id = await setup_classifier(client, labeling, n=5500)
    first = await post(client, start_body(version_id, template_id), agent=False)
    labeling.run_until_done(first.json()["id"])
    second = await post(client, start_body(version_id, template_id, threshold_positive=0.7))
    assert second.status_code == 201, second.text
    assert second.json()["agent_counted_rows"] == 0


async def test_operator_50000_rows_starts_and_writes_no_ledger_row(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    version_id, template_id = await setup_classifier(client, labeling, n=50_000)
    response = await post(client, start_body(version_id, template_id), agent=False)
    assert response.status_code == 201
    assert count(AgentLabelRow) == 0


async def test_a_resume_needs_no_approval(client: httpx.AsyncClient, labeling: Labeling) -> None:
    version_id, template_id = await setup_classifier(client, labeling, n=4000)
    run = (await post(client, start_body(version_id, template_id))).json()
    response = await client.post(f"/api/v1/label-runs/{run['id']}/cancel", headers=AGENT)
    assert response.json()["state"] == "cancelled"
    from src.core.database import async_session_factory
    from src.services import agent_label_ledger

    async with async_session_factory()() as db:  # fill the window to the brim
        await agent_label_ledger.admit(db, version_id, "agent:claude", "label_run", "x", 1000)
        await db.commit()
    resumed = await client.post(f"/api/v1/label-runs/{run['id']}/resume", headers=AGENT)
    assert resumed.status_code == 200 and resumed.json()["state"] == "queued"
    assert count(Approval) == 0


async def test_an_approved_start_whose_plan_grew_is_refused(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    version_id, template_id = await setup_classifier(client, labeling, n=6000)
    gated = (await post(client, start_body(version_id, template_id))).json()
    with sync_session_factory()() as db:
        approval = db.get(Approval, gated["approval_id"])
        assert approval is not None and approval.payload["plan"]["rows_to_score"] == 6000
        approval.payload = {
            **approval.payload,
            "plan": {**approval.payload["plan"], "rows_to_score": 5900},
        }
        from src.core.canonical_json import canonical_sha256

        approval.request_digest = canonical_sha256(approval.payload)
        db.commit()
    decided = await client.post(f"/api/v1/approvals/{gated['approval_id']}/approve", json={})
    assert decided.json()["status"] == "failed"
    assert decided.json()["error"]["code"] == "PLAN_CHANGED"
    assert count(LabelRun) == 0


async def test_an_approved_start_runs_once(client: httpx.AsyncClient, labeling: Labeling) -> None:
    version_id, template_id = await setup_classifier(client, labeling, n=6000)
    gated = (await post(client, start_body(version_id, template_id))).json()
    decided = (
        await client.post(f"/api/v1/approvals/{gated['approval_id']}/approve", json={})
    ).json()
    assert decided["status"] == "executed", decided
    with sync_session_factory()() as db:
        (run,) = db.execute(select(LabelRun)).scalars().all()
    assert run.approval_id == gated["approval_id"] and run.started_by == "agent:claude"
    assert run.agent_counted_rows == 6000


async def test_the_row_filter_changes_the_count_and_the_decision(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    """FR-005.53: a mixed version with {origin: generated} labels only generated rows."""
    set_operator()
    set_role("classifier")
    template_id = await jev_template_id(client)
    v = make_version(
        labeling.data_dir,
        texts(100, "g") + texts(5000, "s"),
        ["generated"] * 100 + ["source"] * 5000,
    )
    assert (await post(client, start_body(v, template_id))).status_code == 202
    filtered = await post(client, start_body(v, template_id, row_filter={"origin": "generated"}))
    assert filtered.status_code == 201
    run = filtered.json()
    assert run["rows_total"] == 100 and run["row_filter"] == {"origin": "generated"}
    final = labeling.run_until_done(run["id"])
    assert sum(final.counts.values()) == 100
    assert all("g " in c.body["prompt"] for c in labeling.millm.calls("/v1/completions"))
    unknown = await post(client, start_body(v, template_id, row_filter={"origin": "made-up"}))
    assert unknown.status_code == 422


async def test_a_preflight_refusal_creates_no_approval_and_no_job(
    client: httpx.AsyncClient, labeling: Labeling, monkeypatch: pytest.MonkeyPatch
) -> None:
    def judge_is_generator(ctx: label_run_preflight.PreflightContext) -> None:
        raise label_run_preflight.PreflightRefused(
            "JUDGE_IS_GENERATOR", "The judge wrote these rows."
        )

    monkeypatch.setattr(label_run_preflight, "PREFLIGHT_CHECKS", [judge_is_generator])
    version_id, template_id = await setup_classifier(client, labeling, n=6000)
    response = await post(client, start_body(version_id, template_id))
    assert response.status_code == 422 and response.json()["error"]["code"] == "JUDGE_IS_GENERATOR"
    assert count(Approval) == 0 and count(Job) == 0 and count(LabelRun) == 0


class TestRefusals:
    async def test_no_operator_name(self, client: httpx.AsyncClient, labeling: Labeling) -> None:
        version_id, template_id = await setup_classifier(client, labeling, n=3)
        from sqlalchemy import text

        with sync_session_factory()() as db:
            db.execute(text("DELETE FROM dw_app_settings WHERE key='operator_name'"))
            db.commit()
        response = await post(client, start_body(version_id, template_id), agent=False)
        assert response.status_code == 422 and response.json()["error"]["code"] == "NO_IDENTITY"
        assert "Settings" in response.json()["error"]["message"]

    @pytest.mark.parametrize(("pos", "neg"), [(0.2, 0.5), (0.5, 0.5), (None, 0.2), (1.5, 0.2)])
    async def test_thresholds_invalid(
        self, client: httpx.AsyncClient, labeling: Labeling, pos: Any, neg: Any
    ) -> None:
        version_id, template_id = await setup_classifier(client, labeling, n=3)
        body = start_body(version_id, template_id, threshold_positive=pos, threshold_negative=neg)
        response = await post(client, body, agent=False)
        assert (
            response.status_code == 422 and response.json()["error"]["code"] == "THRESHOLDS_INVALID"
        )

    async def test_template_bound_to_another_model(
        self, client: httpx.AsyncClient, labeling: Labeling
    ) -> None:
        version_id, template_id = await setup_classifier(client, labeling, n=3)
        set_role("classifier", model="Qwen2.5-7B")
        response = await post(client, start_body(version_id, template_id), agent=False)
        assert (
            response.status_code == 409
            and response.json()["error"]["code"] == "TEMPLATE_MODEL_MISMATCH"
        )

    async def test_a_different_model_loaded_is_refused_with_no_load(
        self, client: httpx.AsyncClient, labeling: Labeling
    ) -> None:
        """Success criterion 6."""
        version_id, template_id = await setup_classifier(client, labeling, n=3)
        labeling.millm.resident = {
            "id": 99,
            "name": "Qwen2.5-7B",
            "repo_id": None,
            "revision": None,
            "quantization": "Q4",
        }
        response = await post(client, start_body(version_id, template_id), agent=False)
        assert response.status_code == 409
        error = response.json()["error"]
        assert error["code"] == "MODEL_NOT_LOADED"
        assert (
            "Load JEV-9B-decision in miLLM" in error["message"] and "Qwen2.5-7B" in error["message"]
        )
        model_work = [
            r
            for r in labeling.millm.requests
            if r.path.startswith("/v1/") and r.path != "/v1/models"
        ]
        assert model_work == []
        assert not [r for r in labeling.millm.requests if r.path.endswith("/load")]

    async def test_another_holder_has_the_lease(
        self, client: httpx.AsyncClient, labeling: Labeling
    ) -> None:
        version_id, template_id = await setup_classifier(client, labeling, n=3)
        labeling.millm.foreign_lease = {"holder": "miforge", "expires_at": "2026-10-07T12:00:00Z"}
        response = await post(client, start_body(version_id, template_id), agent=False)
        assert response.status_code == 409
        assert response.json()["error"]["details"] == {
            "holder": "miforge",
            "expires_at": "2026-10-07T12:00:00Z",
        }

    async def test_unconfigured_role(self, client: httpx.AsyncClient, labeling: Labeling) -> None:
        set_operator()
        version_id = make_version(labeling.data_dir, texts(2))
        template_id = await jev_template_id(client)
        response = await post(client, start_body(version_id, template_id), agent=False)
        assert (
            response.status_code == 409 and response.json()["error"]["code"] == "ROLE_UNCONFIGURED"
        )


async def test_the_key_never_appears_in_any_response_or_row(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    from tests.support.labeling_fixtures import KEY

    version_id, template_id = await setup_classifier(client, labeling, n=5)
    run = (await post(client, start_body(version_id, template_id), agent=False)).json()
    labeling.run_until_done(run["id"])
    bodies = [
        json.dumps(run),
        (await client.get(f"/api/v1/label-runs/{run['id']}")).text,
        (await client.get(f"/api/v1/label-runs/{run['id']}/labels")).text,
        (await client.get("/api/v1/label-runs")).text,
        (
            await client.get(
                "/api/v1/label-runs/plan",
                params={"request": json.dumps(start_body(version_id, template_id))},
            )
        ).text,
    ]
    assert all(KEY not in b for b in bodies)
    assert labeling.millm.calls("/v1/completions")[0].headers["authorization"] == f"Bearer {KEY}"
    from sqlalchemy import text

    with sync_session_factory()() as db:
        for table in (
            "dw_label_runs",
            "dw_labels",
            "dw_jobs",
            "dw_label_run_chunks",
            "dw_model_leases",
        ):
            dump = json.dumps(
                [list(map(str, r)) for r in db.execute(text(f"SELECT * FROM {table}")).all()]
            )
            assert KEY not in dump, table
    for path in labeling.data_dir.rglob("*"):
        if path.is_file():
            assert KEY.encode() not in path.read_bytes(), path
