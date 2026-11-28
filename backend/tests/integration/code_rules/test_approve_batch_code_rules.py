"""Approve all waiting code rules in one decision (ENH-006 D81, operator 2026-10-29).

``POST /api/v1/approvals/approve-batch`` with ``{"kind": "code_rules", "approval_ids": [...]}``:
the same mechanics as the settings batch (D79), a separate kind, never mixed. Every id must be a
waiting ``code_rule_save``; otherwise 409 naming each and nothing is approved. Each request is
approved through the single approve path, so the save replays once per request and writes the
code-rule approve row that a single approval writes (decided by the operator, naming its request),
and each row carries the shared ``batch_id``. The PIN rule is the single code-rule approval's: none.

Negative controls (``0xcc/reviews/approve_all_settings_controls_2026-10-29.md``): the code-rule
eligibility always true (a settings change accepted in a code-rule batch), only the first id
approved, the pre-check skipped; each turns this file red.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy import text

from src.core import settings_pin
from src.core.database import get_sync_engine
from src.runner_contract.hashing import code_sha256
from tests.support import code_rule_fixtures as fx
from tests.support.fake_runner import FakeRunner

from .conftest import AGENT, count, save

ROUTE = "/api/v1/approvals/approve-batch"


def _approve_rows(sha: str) -> list[dict[str, Any]]:
    with get_sync_engine().connect() as conn:
        rows = conn.execute(
            text(
                "SELECT action, reason, changed_by, origin, approval_id FROM "
                "dw_code_rule_approvals WHERE code_sha256 = :s ORDER BY id"
            ),
            {"s": sha},
        ).mappings()
        return [dict(r) for r in rows]


def _rows(ids: list[str]) -> dict[str, dict[str, Any]]:
    with get_sync_engine().connect() as conn:
        found = conn.execute(
            text("SELECT id, status, decided_by, batch_id FROM dw_approvals WHERE id = ANY(:ids)"),
            {"ids": ids},
        ).mappings()
        return {r["id"]: dict(r) for r in found}


async def _queue_two(client: httpx.AsyncClient) -> tuple[list[str], list[str]]:
    """Two waiting code-rule saves (two behaviours, two hashes): their ids and hashes."""
    ids, shas = [], []
    for behaviour in ("fixture-gamma", "fixture-delta"):
        body = fx.save_body(behaviour)
        response = await save(client, body, agent=True)
        assert response.status_code == 202, response.text
        assert response.json()["action"] == "code_rule_save"
        ids.append(response.json()["approval_id"])
        shas.append(code_sha256(body["document"]))
    return ids, shas


async def test_every_listed_code_rule_is_saved_once_with_its_approve_row_and_the_batch_id(
    client: httpx.AsyncClient,
    fake: FakeRunner,
    executor_spy: list[dict[str, Any]],
    operator_name: str,
) -> None:
    ids, shas = await _queue_two(client)
    assert count("dw_config_versions", "kind = 'code_rule'") == 0

    response = await client.post(ROUTE, json={"kind": "code_rules", "approval_ids": ids})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "code_rules" and body["batch_id"].startswith("apb_")
    assert [r["status"] for r in body["results"]] == ["executed", "executed"]

    # The save replayed once per request, with that request's payload.
    assert [c["approval_id"] for c in executor_spy] == ids
    assert count("dw_config_versions", "kind = 'code_rule'") == 2
    # Each hash got the approve row a single approval writes, decided by the operator.
    for approval_id, sha in zip(ids, shas, strict=True):
        assert _approve_rows(sha) == [
            {
                "action": "approve",
                "reason": f"approved the agent's request {approval_id}",
                "changed_by": "Test Operator",
                "origin": "operator",
                "approval_id": approval_id,
            }
        ]
    rows = _rows(ids)
    assert {r["status"] for r in rows.values()} == {"executed"}
    assert {r["decided_by"] for r in rows.values()} == {"Test Operator"}
    assert {r["batch_id"] for r in rows.values()} == {body["batch_id"]}


async def test_a_settings_change_is_refused_in_a_code_rule_batch_and_nothing_runs(
    client: httpx.AsyncClient,
    fake: FakeRunner,
    executor_spy: list[dict[str, Any]],
    operator_name: str,
) -> None:
    ids, _ = await _queue_two(client)
    setting = await client.put("/api/v1/settings/log_level", json={"value": "DEBUG"}, headers=AGENT)
    assert setting.status_code == 202
    setting_id = setting.json()["approval_id"]

    mixed = await client.post(
        ROUTE, json={"kind": "code_rules", "approval_ids": [*ids, setting_id]}
    )
    assert mixed.status_code == 409, mixed.text
    error = mixed.json()["error"]
    assert error["code"] == "APPROVAL_BATCH_REFUSED"
    assert [r["approval_id"] for r in error["details"]["refused"]] == [setting_id]
    assert executor_spy == []
    assert {r["status"] for r in _rows([*ids, setting_id]).values()} == {"pending"}

    # And the other way round: a code rule in a settings batch.
    other = await client.post(
        ROUTE, json={"kind": "settings", "approval_ids": [setting_id, ids[0]]}
    )
    assert other.status_code == 409
    assert [r["approval_id"] for r in other.json()["error"]["details"]["refused"]] == [ids[0]]
    assert {r["status"] for r in _rows([*ids, setting_id]).values()} == {"pending"}


async def test_an_unknown_kind_is_refused(client: httpx.AsyncClient) -> None:
    response = await client.post(ROUTE, json={"kind": "everything", "approval_ids": ["apr_x"]})
    assert response.status_code == 422


async def test_an_agent_cannot_approve_a_code_rule_batch(
    client: httpx.AsyncClient, fake: FakeRunner, operator_name: str
) -> None:
    ids, _ = await _queue_two(client)
    response = await client.post(
        ROUTE, json={"kind": "code_rules", "approval_ids": ids}, headers=AGENT
    )
    assert response.status_code == 403
    assert {r["status"] for r in _rows(ids).values()} == {"pending"}


async def test_the_pin_rule_is_the_single_code_rule_approvals(
    client: httpx.AsyncClient, fake: FakeRunner, operator_name: str
) -> None:
    """A single code-rule approval does not ask for the Settings PIN; neither does the batch."""
    settings_pin.reset_failures()
    assert (await client.put("/api/v1/settings/pin", json={"pin": "4711"})).status_code == 200
    ids, _ = await _queue_two(client)
    single = await client.post(f"/api/v1/approvals/{ids[0]}/approve")
    assert single.status_code == 200, single.text
    batch = await client.post(ROUTE, json={"kind": "code_rules", "approval_ids": ids[1:]})
    assert batch.status_code == 200, batch.text


@pytest.mark.parametrize("kind", ["settings", "code_rules"])
def test_the_kind_is_part_of_the_published_request(kind: str) -> None:
    from src.main import fastapi_app

    schema = fastapi_app.openapi()["components"]["schemas"]["ApproveBatchRequest"]
    assert kind in str(schema["properties"]["kind"])
