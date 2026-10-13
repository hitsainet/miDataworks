"""The batch transport (005 FTASKS 12.4, 18.3; FR-005.35, FR-005.55; criterion: no duplicate batch)."""

from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy import func, select

from src.core.database import sync_session_factory
from src.models.label import Label
from tests.integration.labeling.helpers import setup_classifier, start_body
from tests.support.labeling_fixtures import Labeling


def labels(run_id: str) -> int:
    with sync_session_factory()() as db:
        return int(
            db.execute(
                select(func.count()).select_from(Label).where(Label.label_run_id == run_id)
            ).scalar_one()
        )


async def start(client: httpx.AsyncClient, labeling: Labeling, n: int, **kw: Any) -> dict[str, Any]:
    version_id, template_id = await setup_classifier(
        client, labeling, **({"rows": kw.pop("rows")} if "rows" in kw else {"n": n})
    )
    response = await client.post(
        "/api/v1/label-runs", json=start_body(version_id, template_id, transport="batch", **kw)
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def test_a_batch_run_labels_every_row_under_the_lease_unpacked(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    run = await start(client, labeling, 450)
    assert run["packing"] == "batch"
    final = labeling.run_until_done(run["id"])
    assert final.state == "completed", final.error
    assert labels(run["id"]) == 450
    (created,) = labeling.millm.calls("/v1/batches", "POST")
    assert created.body["pack"] is False and created.body["endpoint"] == "/v1/completions"
    lease = labeling.millm.calls("/api/models/7/lease", "POST")
    assert lease and created.headers["x-millm-lease"].startswith("lease-")
    assert final.batch_id == final.endpoint_snapshot["batches"][0]["id"]
    assert final.endpoint_snapshot["batches"][0]["state"] == "committed"
    assert labeling.millm.calls("/v1/completions") == []  # nothing went through the sync route


async def test_overflow_lines_are_skipped(client: httpx.AsyncClient, labeling: Labeling) -> None:
    run = await start(client, labeling, 0, rows=["fine", "OVERFLOW x", "also fine"])
    final = labeling.run_until_done(run["id"])
    assert final.counts.get("skipped") == 1 and labels(run["id"]) == 3


async def test_a_restart_mid_poll_retakes_the_lease_and_reattaches_without_resubmitting(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    labeling.millm.batch_polls_to_complete = 2
    restarted: list[int] = []

    def restart_once(batch: dict[str, Any]) -> None:
        if not restarted:
            restarted.append(1)
            labeling.millm.restart()

    labeling.millm.on_batch_poll = restart_once
    run = await start(client, labeling, 30)
    final = labeling.run_until_done(run["id"])
    assert final.state == "completed", final.error
    assert len(labeling.millm.calls("/v1/batches", "POST")) == 1  # never resubmitted
    reattach = [
        r
        for r in labeling.millm.requests
        if r.path.endswith("/lease") and r.path.startswith("/v1/batches/")
    ]
    # one refused re-attach with the dead lease, then one with the re-taken lease
    assert [
        r.headers["x-millm-lease"] != reattach[-1].headers["x-millm-lease"] for r in reattach[:-1]
    ] == [True] * (len(reattach) - 1)
    new_lease = reattach[-1].headers["x-millm-lease"]
    assert new_lease in {
        r.headers.get("x-millm-lease")
        for r in labeling.millm.calls("/api/models/7/lease", "DELETE")
    }
    assert len(labeling.millm.calls("/api/models/7/lease", "POST")) == 2
    assert labels(run["id"]) == 30


async def test_a_refused_retake_stops_resumably_and_resume_polls_the_same_batch(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    labeling.millm.batch_polls_to_complete = 2

    def restart_and_block(batch: dict[str, Any]) -> None:
        labeling.millm.restart()
        labeling.millm.foreign_lease = {"holder": "miforge", "expires_at": "x"}

    labeling.millm.on_batch_poll = restart_and_block
    run = await start(client, labeling, 20)
    stopped = labeling.run_until_done(run["id"])
    assert stopped.state == "failed" and stopped.error["code"] == "LEASE_LOST"
    assert "No batch was resubmitted" in stopped.error["message"]
    labeling.millm.on_batch_poll = None
    labeling.millm.foreign_lease = None
    assert (await client.post(f"/api/v1/label-runs/{run['id']}/resume")).status_code == 200
    final = labeling.run_until_done(run["id"])
    assert final.state == "completed", final.error
    assert len(labeling.millm.calls("/v1/batches", "POST")) == 1  # the resume polled the same batch
    assert labels(run["id"]) == 20


async def test_batch_is_refused_off_miLLM_or_for_a_judge(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    version_id, template_id = await setup_classifier(client, labeling, n=3)
    from tests.support.fake_millm import TEI_ORIGIN
    from tests.support.labeling_fixtures import set_role

    set_role(
        "classifier",
        base_url=TEI_ORIGIN,
        model=labeling.tei.model_id,
        protocol="tei_classification",
        api_key=None,
    )
    created = (
        await client.post(
            "/api/v1/decision-templates",
            json={
                "name": "d/x",
                "body": {
                    "kind": "tei_classification",
                    "render": "{text}",
                    "input_fields": ["text"],
                    "label_set": ["a", "b"],
                    "positive_class": "b",
                    "label_map": {"SAFE": "a", "INJECTION": "b"},
                },
            },
        )
    ).json()
    response = await client.post(
        "/api/v1/label-runs", json=start_body(version_id, created["id"], transport="batch")
    )
    assert response.status_code == 422 and response.json()["error"]["code"] == "BATCH_UNSUPPORTED"
