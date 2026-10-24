"""The Label step's row cap, ``row_filter.max_rows`` (novice walk 2026-10-19, finding 4; 005 FPRD
success criterion 12: label 25,000 rows of a larger version).

Pinned here, against the fake miLLM and the real engine:
- the plan counts the cap (rows, distinct keys, rows to label), not the version;
- an agent's approval threshold counts the CAPPED rows: 6,000 rows capped at 5,000 starts, 6,000
  uncapped waits;
- the run labels exactly the capped inputs, the lowest row keys, and nothing else;
- a cancelled run resumes INSIDE the cap: the resume labels the rest of the same inputs and never
  moves on to new ones (the cap is chosen before recorded rows are removed).
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from src.core import cancellation
from src.core.database import sync_session_factory
from src.models.label import Label
from src.models.version import Version
from src.services import label_inputs
from tests.integration.labeling.helpers import AGENT, setup_classifier, start_body
from tests.integration.labeling.test_label_run_worker import request_cancel
from tests.support.labeling_fixtures import Labeling


def labeled_keys(run_id: str) -> list[str]:
    with sync_session_factory()() as db:
        return list(db.execute(select(Label.row_key).where(Label.label_run_id == run_id)).scalars())


def lowest_keys(version_id: str, n: int) -> list[str]:
    with sync_session_factory()() as db:
        version = db.get(Version, version_id)
        files = label_inputs.version_files(version.splits)
    every = sorted(str(k) for k in label_inputs.row_keys(files, None).column("row_key").to_pylist())
    return every[:n]


async def plan(client: httpx.AsyncClient, body: dict[str, Any], agent: bool = False) -> dict:
    response = await client.get(
        "/api/v1/label-runs/plan",
        params={"request": json.dumps(body)},
        headers=AGENT if agent else {},
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


async def test_the_plan_counts_the_cap(client: httpx.AsyncClient, labeling: Labeling) -> None:
    version_id, template_id = await setup_classifier(client, labeling, n=800)
    whole = await plan(client, start_body(version_id, template_id))
    capped = await plan(client, start_body(version_id, template_id, row_filter={"max_rows": 250}))
    assert (whole["rows_total"], whole["rows_to_score"]) == (800, 800)
    assert (capped["rows_total"], capped["rows_to_score"]) == (250, 250)
    assert capped["row_coverage"]["rows"] == 250 and capped["row_coverage"]["row_keys"] == 250
    # a cap above the version is the version
    above = await plan(client, start_body(version_id, template_id, row_filter={"max_rows": 5000}))
    assert above["rows_total"] == 800


async def test_an_empty_row_filter_is_refused(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    version_id, template_id = await setup_classifier(client, labeling, n=5)
    response = await client.get(
        "/api/v1/label-runs/plan",
        params={"request": json.dumps(start_body(version_id, template_id, row_filter={}))},
    )
    assert response.status_code == 422


async def test_the_approval_threshold_counts_the_capped_rows(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    version_id, template_id = await setup_classifier(client, labeling, n=6000)
    uncapped = await plan(client, start_body(version_id, template_id), agent=True)
    assert uncapped["approval_needed"] is True and uncapped["rows_to_score"] == 6000
    body = start_body(version_id, template_id, row_filter={"max_rows": 5000})
    assert (await plan(client, body, agent=True))["approval_needed"] is False
    started = await client.post("/api/v1/label-runs", json=body, headers=AGENT)
    assert started.status_code == 201, started.text
    assert started.json()["agent_counted_rows"] == 5000
    assert started.json()["row_filter"] == {"max_rows": 5000}


async def test_the_run_labels_exactly_the_capped_inputs(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    version_id, template_id = await setup_classifier(client, labeling, n=700)
    response = await client.post(
        "/api/v1/label-runs", json=start_body(version_id, template_id, row_filter={"max_rows": 300})
    )
    assert response.status_code == 201, response.text
    run = response.json()
    final = labeling.run_until_done(run["id"])
    assert final.state == "completed", final.error
    assert sorted(labeled_keys(run["id"])) == lowest_keys(version_id, 300)
    assert labeling.millm.scoring_calls == 300
    detail = (await client.get(f"/api/v1/label-runs/{run['id']}")).json()
    assert detail["rows_total"] == 300 and detail["rows_done"] == 300
    # finding 13: a finished run states how long its jobs worked and the rate
    timing = detail["timing"]
    assert timing["rows_scored"] == 300 and timing["jobs_timed"] == 1
    assert timing["active_seconds"] > 0
    # active_seconds is served rounded to the millisecond and the rate is computed from the unrounded
    # span, so on a sub-second run the rate is bounded by the rounding, not by a fixed rel=1e-3
    # (flaked 2026-10-20: 801.134 vs 300/0.374 = 802.139 on a 0.3745 s run).
    active = timing["active_seconds"]
    low, high = 300 / (active + 0.0005), 300 / max(active - 0.0005, 1e-9)
    assert low - 0.0005 <= timing["rows_per_second"] <= high + 0.0005


async def test_a_resume_stays_inside_the_cap(
    client: httpx.AsyncClient, labeling: Labeling, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cancellation, "DEFAULT_POLL_INTERVAL_S", 0.0)
    version_id, template_id = await setup_classifier(client, labeling, n=1500)
    response = await client.post(
        "/api/v1/label-runs", json=start_body(version_id, template_id, row_filter={"max_rows": 600})
    )
    assert response.status_code == 201, response.text
    run = response.json()
    job_id = labeling.job_for(run["id"])
    labeling.millm.on_score = lambda n: request_cancel(job_id) if n == 250 else None
    assert labeling.run_job(job_id)["status"] == "cancelled"
    before = len(labeled_keys(run["id"]))
    assert 250 <= before < 600

    labeling.millm.on_score = None
    resumed = await client.post(f"/api/v1/label-runs/{run['id']}/resume")
    assert resumed.status_code == 200, resumed.text
    final = labeling.run_until_done(run["id"])
    assert final.state == "completed", final.error
    keys = labeled_keys(run["id"])
    assert len(keys) == len(set(keys)) == 600
    assert sorted(keys) == lowest_keys(version_id, 600)
    assert labeling.millm.scoring_calls == 600
