"""Try a sample, the keep-share preview and Test endpoint over REST (005 FTASKS 5.8, 7.x)."""

from __future__ import annotations

import httpx
from sqlalchemy import func, select

from src.core.database import sync_session_factory
from src.models.label import Label
from tests.integration.labeling.helpers import QUESTION, setup_classifier
from tests.support.labeling_fixtures import KEY, Labeling


async def test_sample_scores_with_refuse_load_no_lease_and_writes_nothing(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    version_id, template_id = await setup_classifier(client, labeling, n=30)
    body = {
        "input_version_id": version_id,
        "role": "classifier",
        "template_id": template_id,
        "question": QUESTION,
        "field_map": {"text": "text"},
        "rows": 5,
        "threshold_positive": 0.5,
        "threshold_negative": 0.2,
    }
    response = await client.post("/api/v1/labeling/sample", json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert len(result["rows"]) == 5 and result["steering_state"] == "unsteered (scoring mode)"
    for row in result["rows"]:
        assert 0 <= row["probability"] <= 1 and row["outcome"] in {
            "positive",
            "negative",
            "excluded",
        }
    calls = labeling.millm.calls("/v1/completions")
    assert len(calls) == 5
    assert all(
        c.headers["x-millm-load-policy"] == "refuse" and "x-millm-lease" not in c.headers
        for c in calls
    )
    assert [r for r in labeling.millm.requests if "lease" in r.path] == []
    with sync_session_factory()() as db:
        assert db.execute(select(func.count()).select_from(Label)).scalar_one() == 0
    assert KEY not in response.text
    too_many = await client.post("/api/v1/labeling/sample", json={**body, "rows": 21})
    assert too_many.status_code == 422


async def test_keep_share_preview_reports_share_interval_and_sample_size(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    version_id, template_id = await setup_classifier(client, labeling, n=600)
    body = {
        "input_version_id": version_id,
        "template_id": template_id,
        "question": QUESTION,
        "field_map": {"text": "text"},
        "threshold_positive": 0.5,
        "threshold_negative": 0.2,
        "seed": 3,
    }
    response = await client.post("/api/v1/labeling/keep-share", json=body)
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    assert response.json()["room"] == f"dataworks/label-previews/{job_id}"
    sent = [s for s in labeling.sent if s[0] == "midataworks.labeling.run_label_preview"]
    assert len(sent) == 1 and sent[0][1] == [job_id]
    from src.workers import label_run_tasks

    assert label_run_tasks.preview_job(job_id)["outcome"] == "completed"
    result = (await client.get(f"/api/v1/labeling/keep-share/{job_id}")).json()
    assert result["status"] == "completed" and result["n"] == 400 and result["seed"] == 3
    assert result["lo"] <= result["share"] <= result["hi"]
    assert len(labeling.millm.calls("/v1/completions")) == 400
    assert [r for r in labeling.millm.requests if "lease" in r.path] == []
    with sync_session_factory()() as db:
        assert db.execute(select(func.count()).select_from(Label)).scalar_one() == 0
    capped = await client.post("/api/v1/labeling/keep-share", json={**body, "sample_rows": 1001})
    assert capped.status_code == 422
    # the estimate travels onto the run and sits beside the actual share after it
    from tests.integration.labeling.helpers import start_body

    run = (
        await client.post(
            "/api/v1/label-runs", json=start_body(version_id, template_id, keep_share_job_id=job_id)
        )
    ).json()
    assert run["keep_share_estimate"]["n"] == 400
    final = labeling.run_until_done(run["id"])
    assert (
        final.keep_share_actual is not None
        and final.keep_share_estimate["share"] == result["share"]
    )


async def test_endpoint_test_route(client: httpx.AsyncClient, labeling: Labeling) -> None:
    await setup_classifier(client, labeling, n=1)
    result = (await client.post("/api/v1/endpoint-roles/classifier/test")).json()
    assert result["reachable"] and result["protocol_ok"] and result["server_kind"] == "millm"
    assert result["resident_model"] == "JEV-9B-decision" and result["lease_state"] == "free"
    assert result["queue"]["queue_pending"] == 0
    labeling.millm.lease_supported = False
    assert (await client.post("/api/v1/endpoint-roles/classifier/test")).json()[
        "lease_state"
    ] == "not served"
    unconfigured = await client.post("/api/v1/endpoint-roles/judge/test")
    assert (
        unconfigured.status_code == 409
        and unconfigured.json()["error"]["code"] == "ROLE_UNCONFIGURED"
    )
