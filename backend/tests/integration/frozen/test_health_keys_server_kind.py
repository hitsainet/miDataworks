"""DR-36 and DR-37: health's sibling keys and the stored ``server_kind`` value stay (feature 020).

The frontend reads ``dependencies.millm`` / ``dependencies.mistudio`` (PD-10); a label run stored
with ``server_kind: "millm"`` before the rename must resume on the miServer lease path."""

from __future__ import annotations

import httpx
from sqlalchemy import text

from src.core.database import get_sync_engine
from tests.integration.labeling.helpers import setup_classifier, start_body
from tests.support.frozen import exercises
from tests.support.labeling_fixtures import Labeling, labeling  # noqa: F401 - fixture


@exercises("DR-36", literals=("millm", "mistudio"))
async def test_dr36_health_keys_are_millm_and_mistudio(
    client: httpx.AsyncClient, literal: str
) -> None:
    body = (await client.get("/api/health")).json()
    assert literal in body["dependencies"], f"dependencies.{literal} is gone"
    assert {"ok", "configured", "address_source"} <= set(body["dependencies"][literal])


@exercises("DR-37", literals=("millm",))
async def test_dr37_stored_millm_server_kind_resumes_on_the_lease_path(
    client: httpx.AsyncClient,
    labeling: Labeling,  # noqa: F811
    literal: str,
) -> None:
    version_id, template_id = await setup_classifier(client, labeling, n=3)
    started = await client.post("/api/v1/label-runs", json=start_body(version_id, template_id))
    assert started.status_code == 201, started.text
    run_id = started.json()["id"]
    with get_sync_engine().connect() as conn:
        stored = conn.execute(
            text("SELECT endpoint_snapshot->>'server_kind' FROM dw_label_runs WHERE id = :i"),
            {"i": run_id},
        ).scalar()
    assert stored == literal, "the run is stored with the frozen server kind"
    final = labeling.run_until_done(run_id)
    assert final.state == "completed", final.error
    assert len(labeling.millm.calls("/api/models/7/lease", "POST")) == 1, "the lease path ran"
