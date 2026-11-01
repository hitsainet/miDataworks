"""A calibration set built from a calibration-labeling queue (006 FTASKS 6.4, 8.1; FR-006.24,
FR-006.26, T-25): operator decisions only; model output hidden by default."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select

from src.core.database import sync_session_factory
from src.models.calibration import CalibrationSetLabel
from tests.support.calibration_fixtures import LABELS, QUESTION, make_run, make_version, row_key

AGENT = {"X-Dataworks-Agent": "agent:dataworks-mcp"}


async def labeling_queue(client: httpx.AsyncClient, version: str, **extra: Any) -> dict[str, Any]:
    r = await client.post(
        "/api/v1/review-queues",
        json={
            "kind": "calibration_labeling",
            "version_id": version,
            "question": QUESTION,
            "label_set": LABELS,
            "size": 12,
            "seed": 4,
            **extra,
        },
    )
    assert r.status_code == 201, r.text
    return dict(r.json())


async def test_model_output_is_hidden_by_default_and_recorded(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    rows = [{"text": f"t{i}"} for i in range(40)]
    version = make_version(data_dir, rows)
    run = make_run(version, {row_key(r["text"]): 0.5 for r in rows})
    queue = await labeling_queue(client, version, label_run_id=run.id)
    assert (
        queue["show_model_output"] is False and queue["sample_spec"]["strata"] == "probability_bin"
    )
    page = (await client.get(f"/api/v1/review-queues/{queue['id']}/items")).json()["items"]
    assert all(i["model_snapshot"] is None and i["model_output_hidden"] for i in page)
    first = page[0]["id"]
    d = await client.post(
        f"/api/v1/review-items/{first}/decisions",
        json={"decision": "override", "override_label": "humorous"},
    )
    assert d.status_code == 201 and d.json()["model_output_visible"] is False
    assert d.json()["reason"] == "label assigned by the reviewer"


async def test_an_agent_accept_on_the_same_row_never_enters_the_set(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    rows = [{"text": f"t{i}"} for i in range(40)]
    version = make_version(data_dir, rows)
    run = make_run(version, {row_key(r["text"]): 0.9 for r in rows})
    queue = await labeling_queue(client, version, label_run_id=run.id, show_model_output=True)
    page = (await client.get(f"/api/v1/review-queues/{queue['id']}/items")).json()["items"]
    # operator labels rows 0..5 (three each way); an agent accepts row 6 (model says humorous)
    for n, item in enumerate(page[:6]):
        label = "humorous" if n % 2 else "not_humorous"
        await client.post(
            f"/api/v1/review-items/{item['id']}/decisions",
            json={"decision": "override", "override_label": label},
        )
    agent_item = page[6]
    a = await client.post(
        f"/api/v1/review-items/{agent_item['id']}/decisions",
        json={"decision": "accept"},
        headers=AGENT,
    )
    assert a.status_code == 201
    # and an agent accept on an operator-labeled row does not replace the operator's label
    await client.post(
        f"/api/v1/review-items/{page[0]['id']}/decisions",
        json={"decision": "accept"},
        headers=AGENT,
    )
    built = await client.post(
        "/api/v1/calibration-sets/from-review", json={"queue_id": queue["id"]}
    )
    assert built.status_code == 201, built.text
    data = built.json()
    assert data["source_kind"] == "review" and data["counts"]["labeled"] == 6
    assert data["provenance"]["deciders"] == [operator_name]
    assert data["provenance"]["decisions_with_model_output_visible"] == 6
    with sync_session_factory()() as s:
        labels = {
            x.row_key: x.human_label
            for x in s.execute(
                select(CalibrationSetLabel).where(
                    CalibrationSetLabel.calibration_set_id == data["id"]
                )
            ).scalars()
        }
    assert agent_item["row_key"] not in labels
    assert labels[page[0]["row_key"]] == "not_humorous"


async def test_a_non_labeling_queue_cannot_become_a_set(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    rows = [{"text": f"t{i}"} for i in range(10)]
    version = make_version(data_dir, rows)
    run = make_run(version, {row_key(r["text"]): 0.5 for r in rows})
    q = (
        await client.post(
            "/api/v1/review-queues",
            json={"kind": "label_review", "label_run_id": run.id, "size": 5},
        )
    ).json()
    r = await client.post("/api/v1/calibration-sets/from-review", json={"queue_id": q["id"]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "QUEUE_KIND_INVALID"
