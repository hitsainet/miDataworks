"""Every set's sends in one reading (2026-11-11).

``dataworks_list_detector_sends`` required ``set_id``, so "is any send in flight anywhere?" (the
drain check before a deploy) took one call per detector set. ``GET /detector-sends`` answers it
once, with ``state=active`` (queued or running) and ``set_id`` to narrow. Driven through REST and
through the PRODUCTION MCP registry (``build_server``) over the in-process app, on a real
PostgreSQL; the rows differ in set, state and age, so a filter that is not applied shows.
"""

from __future__ import annotations

import typing
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from src.api.v1.endpoints.detector_sets import SendStateFilter
from src.core.database import sync_session_factory
from src.core.ids import new_id
from src.models import DetectorSend, DetectorSet
from src.models.detector_send import ACTIVE_SEND_STATES, SEND_STATES
from tests.support import db_factories
from tests.support.mcp_live import call, production_mcp

API = "/api/v1"
T0 = datetime(2026, 11, 1, tzinfo=UTC)


def _sends() -> dict[str, Any]:
    """Two sets: A has running (newest), completed, failed; B has queued and cancelled."""
    out: dict[str, Any] = {}
    with sync_session_factory()() as db:
        for label in ("a", "b"):
            row = DetectorSet(
                id=new_id("dts"),
                name=f"set-{label}-{new_id('x')[-8:].lower()}",
                created_by="T",
                created_by_origin="operator",
            )
            db.add(row)
            db.flush()
            out[label] = row.id
        plan = [
            ("a", "completed", 1),
            ("b", "cancelled", 2),
            ("a", "failed", 3),
            ("b", "queued", 4),
            ("a", "running", 5),
        ]
        for set_label, state, hours in plan:
            job = db_factories.job(db, kind="selftest")
            send = DetectorSend(
                id=new_id("dsn"),
                set_id=out[set_label],
                job_id=job.id,
                job_ids=[job.id],
                snapshot={"roles": []},
                snapshot_sha256="0" * 64,
                checks=[],
                plan={"roles": []},
                mistudio_base_url="http://m",
                approval_digest="1" * 64,
                state=state,
                started_by="T",
                started_by_origin="operator",
                created_at=T0 + timedelta(hours=hours),
            )
            db.add(send)
            db.flush()
            out[state] = send.id
        db.commit()
    return out


async def _get(client: httpx.AsyncClient, **params: Any) -> dict[str, Any]:
    response = await client.get(f"{API}/detector-sends", params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _states(body: dict[str, Any], ids: dict[str, Any]) -> list[str]:
    by_id = {v: k for k, v in ids.items()}
    return [by_id[item["id"]] for item in body["items"]]


def test_the_state_filter_names_every_send_state_and_active() -> None:
    assert set(typing.get_args(SendStateFilter)) == set(SEND_STATES) | {"active"}
    assert set(ACTIVE_SEND_STATES) == {"queued", "running"}


async def test_every_sets_sends_newest_first_with_the_total(client: httpx.AsyncClient) -> None:
    ids = _sends()
    body = await _get(client, page=1, limit=50)
    assert _states(body, ids) == ["running", "queued", "failed", "cancelled", "completed"]
    assert {item["set_id"] for item in body["items"]} == {ids["a"], ids["b"]}
    assert (body["total"], body["page"], body["limit"]) == (5, 1, 50)
    second = await _get(client, page=2, limit=2)
    assert _states(second, ids) == ["failed", "cancelled"]
    assert second["total"] == 5


async def test_active_is_queued_or_running_across_sets(client: httpx.AsyncClient) -> None:
    ids = _sends()
    body = await _get(client, state="active", page=1, limit=50)
    assert _states(body, ids) == ["running", "queued"]
    assert body["total"] == 2
    assert _states(await _get(client, state="failed"), ids) == ["failed"]
    assert (await _get(client, state="rejected", page=1, limit=50))["total"] == 0


async def test_set_id_narrows_to_one_set_and_combines_with_state(
    client: httpx.AsyncClient,
) -> None:
    ids = _sends()
    assert _states(await _get(client, set_id=ids["b"]), ids) == ["queued", "cancelled"]
    assert _states(await _get(client, set_id=ids["a"], state="active"), ids) == ["running"]
    # the per-set list the screen and miWorkbench read answers the same rows
    legacy = await client.get(f"{API}/detector-sets/{ids['a']}/sends")
    assert legacy.status_code == 200
    assert [i["id"] for i in legacy.json()["items"]] == [
        i["id"] for i in (await _get(client, set_id=ids["a"]))["items"]
    ]


async def test_an_unknown_set_or_state_is_refused(client: httpx.AsyncClient) -> None:
    _sends()
    missing = await client.get(f"{API}/detector-sends", params={"set_id": "dts_missing"})
    assert missing.status_code == 404, missing.text
    assert "set_not_found" in missing.text
    bad = await client.get(f"{API}/detector-sends", params={"state": "in_flight"})
    assert bad.status_code == 422, bad.text


async def test_the_production_mcp_tool_answers_the_drain_check_in_one_call(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = _sends()
    mcp, _ = production_mcp(monkeypatch)
    body = await call(mcp, "dataworks_list_detector_sends", {"state": "active"})
    assert [item["id"] for item in body["items"]] == [ids["running"], ids["queued"]]
    assert body["total"] == 2
    one = await call(mcp, "dataworks_list_detector_sends", {"set_id": ids["b"]})
    assert [item["id"] for item in one["items"]] == [ids["queued"], ids["cancelled"]]
