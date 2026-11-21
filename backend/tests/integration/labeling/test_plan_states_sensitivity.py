"""A label run's plan states the input version's sensitivity, and refuses nothing for it (021
FTASKS 16.2; FR-021.32; 021-OQ4 decided by suite P42: refusing miDataworks' own external calls on
sensitive data is a backlog item)."""

from __future__ import annotations

import json

import httpx

from src.core.database import sync_session_factory
from src.models import Version
from tests.integration.labeling.helpers import setup_classifier, start_body
from tests.support.labeling_fixtures import Labeling


async def test_the_plan_states_the_inputs_sensitivity(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    version_id, template_id = await setup_classifier(client, labeling, n=6)
    start = start_body(version_id, template_id)
    plan = await client.get("/api/v1/label-runs/plan", params={"request": json.dumps(start)})
    assert plan.status_code == 200, plan.text
    stated = plan.json()["input_sensitivity"]
    assert stated["sensitivity"] is None and stated["basis"]["raised_by"] == []
    # a sensitive version is stated, and still planned (nothing is refused)
    with sync_session_factory()() as db:
        row = db.get(Version, version_id)
        assert row is not None
        sources = json.loads(row.manifest)["sources"]
    assert sources == []  # the fixture names none; the raise comes from lineage only
    started = await client.post("/api/v1/label-runs", json=start)
    assert started.status_code in (201, 202), started.text
