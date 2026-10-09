"""The audit before export (006 FTASKS 9.1 – 9.5; FR-006.27 – FR-006.29)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.support.calibration_fixtures import make_run, make_version, row_key

AGENT = {"X-Dataworks-Agent": "agent:dataworks-mcp"}


@pytest.fixture
def version_with_run(client: httpx.AsyncClient, data_dir: Path) -> tuple[str, str]:
    rows = [{"text": f"row {i}", "origin_tag": "gen" if i % 3 else "src"} for i in range(300)]
    version = make_version(data_dir, rows)
    run = make_run(version, {row_key(r["text"]): (i % 10) / 10 + 0.05 for i, r in enumerate(rows)})
    return version, run.id


async def draw(client: httpx.AsyncClient, version: str, **body: Any) -> httpx.Response:
    return await client.post(f"/api/v1/versions/{version}/audit", json=body)


async def draw_run(client: httpx.AsyncClient, vr: tuple[str, str], **body: Any) -> httpx.Response:
    return await draw(client, vr[0], label_run_id=vr[1], **body)


async def all_items(client: httpx.AsyncClient, queue_id: str) -> list[dict[str, Any]]:
    return list(
        (await client.get(f"/api/v1/review-queues/{queue_id}/items?limit=200")).json()["items"]
    )


async def test_status_none_before_any_draw(
    client: httpx.AsyncClient, version_with_run: Any
) -> None:
    version, _ = version_with_run
    status = (await client.get(f"/api/v1/versions/{version}/audit")).json()
    assert status["state"] == "none" and status["result"] is None


async def test_default_draw_is_100_rows_stratified_by_label_and_band(
    client: httpx.AsyncClient, operator_name: str, version_with_run: Any
) -> None:
    version, run_id = version_with_run
    response = await draw_run(client, version_with_run, seed=3)
    assert response.status_code == 201, response.text
    status = response.json()
    assert status["state"] == "in_progress" and status["size"] == 100 and status["decided"] == 0
    assert status["strata"]["kind"] == "effective_label_x_probability_band"
    items = await all_items(client, status["queue_id"])
    assert len(items) == 100
    strata = {i["stratum"] for i in items}
    assert {"humorous|at_or_above", "not_humorous|at_or_below"} <= strata
    assert any(s.endswith("|excluded") for s in strata)


async def test_caller_strata_columns_are_accepted(
    client: httpx.AsyncClient, operator_name: str, version_with_run: Any
) -> None:
    version, _ = version_with_run
    status = (
        await draw_run(client, version_with_run, size=60, strata_columns=["origin_tag"], seed=1)
    ).json()
    items = await all_items(client, status["queue_id"])
    assert {i["stratum"] for i in items} == {'{"origin_tag":"gen"}', '{"origin_tag":"src"}'}
    assert status["strata"]["columns"] == ["origin_tag"]


@pytest.mark.parametrize("size", [49, 101])
async def test_size_out_of_range_is_refused_with_a_next_step(
    client: httpx.AsyncClient, operator_name: str, version_with_run: Any, size: int
) -> None:
    version, _ = version_with_run
    response = await draw_run(client, version_with_run, size=size)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "AUDIT_SIZE_OUT_OF_RANGE"
    assert "50 to 100" in error["message"] and "100 is the default" in error["message"]


async def test_the_operator_completes_it_and_the_result_counts(
    client: httpx.AsyncClient, operator_name: str, version_with_run: Any
) -> None:
    version, _ = version_with_run
    status = (await draw_run(client, version_with_run, size=50, seed=2)).json()
    items = await all_items(client, status["queue_id"])
    for n, item in enumerate(items):
        if n == 0:
            body: dict[str, Any] = {
                "decision": "override",
                "override_label": "humorous",
                "reason": "x",
            }
        elif n == 1:
            body = {"decision": "flag", "reason": "unsure"}
        else:
            body = {"decision": "accept"}
        r = await client.post(f"/api/v1/review-items/{item['id']}/decisions", json=body)
        assert r.status_code == 201, r.text
    done = (await client.get(f"/api/v1/versions/{version}/audit")).json()
    assert done["state"] == "complete"
    assert done["result"] == {
        "accept": 48,
        "override": 1,
        "flag": 1,
        "decided": 50,
        "size": 50,
        "agreement_share": 48 / 50,
    }
    queue = (await client.get(f"/api/v1/review-queues/{status['queue_id']}")).json()
    assert queue["state"] == "closed"


async def test_an_agent_accept_on_the_last_row_leaves_it_in_progress(
    client: httpx.AsyncClient, operator_name: str, version_with_run: Any
) -> None:
    version, _ = version_with_run
    status = (await draw_run(client, version_with_run, size=50, seed=2)).json()
    items = await all_items(client, status["queue_id"])
    for item in items[:-1]:
        await client.post(
            f"/api/v1/review-items/{item['id']}/decisions", json={"decision": "accept"}
        )
    last = await client.post(
        f"/api/v1/review-items/{items[-1]['id']}/decisions",
        json={"decision": "accept"},
        headers=AGENT,
    )
    assert last.status_code == 201
    now = (await client.get(f"/api/v1/versions/{version}/audit")).json()
    assert now["state"] == "in_progress" and now["decided"] == 49


async def test_a_redraw_supersedes_and_earlier_operator_decisions_count(
    client: httpx.AsyncClient, operator_name: str, version_with_run: Any
) -> None:
    version, _ = version_with_run
    first = (await draw_run(client, version_with_run, size=50, seed=2)).json()
    items = await all_items(client, first["queue_id"])
    for item in items[:10]:
        await client.post(
            f"/api/v1/review-items/{item['id']}/decisions", json={"decision": "accept"}
        )
    second = (await draw_run(client, version_with_run, size=50, seed=2)).json()
    assert second["audit_id"] != first["audit_id"] and second["state"] == "in_progress"
    assert second["decided"] == 10  # the same seed draws the same rows; prior decisions count
    old_queue = (await client.get(f"/api/v1/review-queues/{first['queue_id']}")).json()
    assert old_queue["state"] == "closed"
    status = (await client.get(f"/api/v1/versions/{version}/audit")).json()
    assert status["audit_id"] == second["audit_id"]


async def test_an_audit_without_a_question_source_is_refused(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    version = make_version(data_dir, [{"text": f"t{i}"} for i in range(80)])
    r = await draw(client, version)
    assert r.status_code == 422 and r.json()["error"]["code"] == "AUDIT_NEEDS_QUESTION"
    ok = await draw(client, version, question="Is this row fit to publish?")
    assert ok.status_code == 201 and ok.json()["size"] == 100


async def test_an_audit_in_progress_keeps_its_version_from_deletion(
    client: httpx.AsyncClient, operator_name: str, version_with_run: Any
) -> None:
    """006's REFERENCE_CHECKERS entry (002 FR-002.37): finish the audit before deleting."""
    status = (await draw_run(client, version_with_run, size=50, seed=2)).json()
    refused = await client.request(
        "DELETE", f"/api/v1/versions/{version_with_run[0]}", json={"reason": "superseded"}
    )
    assert refused.status_code == 409, refused.text
    assert status["audit_id"] in refused.text
