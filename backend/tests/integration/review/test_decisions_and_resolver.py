"""Queues, decisions, the append-only rule and the effective-label resolver (006 FTASKS 8.1, 8.5,
8.6, 8.8, 2.4, 2.7)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from src.core.database import sync_session_factory
from src.services.review import effective_label
from tests.support.calibration_fixtures import QUESTION, make_run, make_version, row_key


def texts(n: int) -> list[dict[str, Any]]:
    return [{"text": f"row {i}", "grade": float(i % 4)} for i in range(n)]


@pytest.fixture
def labeled(client: httpx.AsyncClient, data_dir: Path) -> tuple[str, str, list[str]]:
    rows = texts(60)
    version = make_version(data_dir, rows)
    rng = np.random.default_rng(3)
    scores = {row_key(r["text"]): float(rng.random()) for r in rows}
    run = make_run(version, scores)
    return version, run.id, [row_key(r["text"]) for r in rows]


async def review_queue(
    client: httpx.AsyncClient, run_id: str, keys: list[str] | None = None
) -> dict[str, Any]:
    body: dict[str, Any] = {"kind": "label_review", "label_run_id": run_id}
    if keys is not None:
        body["row_keys"] = keys
    else:
        body.update(size=20, seed=1)
    response = await client.post("/api/v1/review-queues", json=body)
    assert response.status_code == 201, response.text
    return dict(response.json())


async def items(client: httpx.AsyncClient, queue_id: str) -> list[dict[str, Any]]:
    return list(
        (await client.get(f"/api/v1/review-queues/{queue_id}/items?limit=200")).json()["items"]
    )


async def decide(client: httpx.AsyncClient, item_id: str, **body: Any) -> httpx.Response:
    return await client.post(f"/api/v1/review-items/{item_id}/decisions", json=body)


async def test_label_review_queue_with_snapshots_and_text(
    client: httpx.AsyncClient, operator_name: str, labeled: Any
) -> None:
    _, run_id, keys = labeled
    queue = await review_queue(client, run_id)
    assert queue["items"] == 20 and queue["decided"] == 0 and queue["show_model_output"] is True
    page = await items(client, queue["id"])
    assert len(page) == 20
    first = page[0]
    assert first["model_snapshot"]["label_run_id"] == run_id
    assert first["text"]["text"].startswith("row ")
    assert first["model_output_hidden"] is False


async def test_a_queue_from_row_keys_refuses_unknown_keys(
    client: httpx.AsyncClient, operator_name: str, labeled: Any
) -> None:
    _, run_id, keys = labeled
    queue = await review_queue(client, run_id, keys[:5])
    assert queue["items"] == 5 and queue["sample_spec"]["source"] == "row_keys"
    bad = await client.post(
        "/api/v1/review-queues",
        json={"kind": "label_review", "label_run_id": run_id, "row_keys": [keys[0], "f" * 64]},
    )
    assert bad.status_code == 404
    assert bad.json()["error"]["code"] == "ROW_KEY_UNKNOWN"
    assert bad.json()["error"]["details"]["row_keys"] == ["f" * 64]


async def test_decisions_are_recorded_with_who_and_history(
    client: httpx.AsyncClient, operator_name: str, labeled: Any
) -> None:
    _, run_id, keys = labeled
    queue = await review_queue(client, run_id, keys[:3])
    item = (await items(client, queue["id"]))[0]
    accepted = await decide(client, item["id"], decision="accept")
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["reason"] == "accepted the model label"
    assert accepted.json()["decided_by"] == operator_name
    over = await decide(
        client, item["id"], decision="override", override_label="not_humorous", reason="flat"
    )
    assert over.status_code == 201
    history = (await client.get(f"/api/v1/review-items/{item['id']}/decisions")).json()
    assert [h["decision"] for h in history] == ["accept", "override"]
    page = await items(client, queue["id"])
    assert page[0]["latest_decision"]["decision"] == "override"
    assert (await client.get(f"/api/v1/review-queues/{queue['id']}")).json()["decided"] == 1


async def test_decisions_are_append_only_in_the_database(
    client: httpx.AsyncClient, operator_name: str, labeled: Any
) -> None:
    _, run_id, keys = labeled
    queue = await review_queue(client, run_id, keys[:1])
    item = (await items(client, queue["id"]))[0]
    did = (await decide(client, item["id"], decision="accept")).json()["id"]
    for sql in (
        "UPDATE dw_review_decisions SET reason = 'edited' WHERE id = :id",
        "DELETE FROM dw_review_decisions WHERE id = :id",
    ):
        with sync_session_factory()() as s, pytest.raises(DBAPIError, match="append-only"):
            s.execute(text(sql), {"id": did})
            s.commit()


# --- 8.8 edge cases ---------------------------------------------------------------------------


async def test_empty_operator_name_is_refused(client: httpx.AsyncClient, labeled: Any) -> None:
    from tests.support.calibration_fixtures import QUESTION as _Q  # noqa: F401

    _, run_id, keys = labeled
    from tests.support.labeling_fixtures import set_operator

    set_operator()
    queue = await review_queue(client, run_id, keys[:1])
    set_operator("  ")
    item = (await items(client, queue["id"]))[0]
    r = await decide(client, item["id"], decision="accept")
    assert r.status_code == 422 and r.json()["error"]["code"] == "NO_IDENTITY"
    assert "Settings" in r.json()["error"]["message"]


async def test_label_outside_the_set_is_refused(
    client: httpx.AsyncClient, operator_name: str, labeled: Any
) -> None:
    _, run_id, keys = labeled
    item = (await items(client, (await review_queue(client, run_id, keys[:1]))["id"]))[0]
    r = await decide(
        client, item["id"], decision="override", override_label="sarcastic", reason="x"
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "DECISION_INVALID"
    assert r.json()["error"]["details"]["allowed_labels"] == ["humorous", "not_humorous"]


@pytest.mark.parametrize("decision", ["override", "flag"])
async def test_missing_reason_is_refused(
    client: httpx.AsyncClient, operator_name: str, labeled: Any, decision: str
) -> None:
    _, run_id, keys = labeled
    item = (await items(client, (await review_queue(client, run_id, keys[:1]))["id"]))[0]
    body: dict[str, Any] = {"decision": decision}
    if decision == "override":
        body["override_label"] = "humorous"
    r = await decide(client, item["id"], **body)
    assert r.status_code == 422 and r.json()["error"]["code"] == "DECISION_INVALID"


async def test_reject_on_a_label_queue_is_refused(
    client: httpx.AsyncClient, operator_name: str, labeled: Any
) -> None:
    _, run_id, keys = labeled
    item = (await items(client, (await review_queue(client, run_id, keys[:1]))["id"]))[0]
    r = await decide(client, item["id"], decision="reject", reason="bad")
    assert r.status_code == 422 and r.json()["error"]["code"] == "DECISION_INVALID"


async def test_constraints_refuse_bad_rows_directly(clean_db: None) -> None:
    with sync_session_factory()() as s, pytest.raises(IntegrityError):
        s.execute(
            text(
                "INSERT INTO dw_calibration_targets (id, question_hash, question, target, set_by, "
                "set_by_origin) VALUES ('ct_1', :h, 'q', 0.8, 'agent:x', 'agent')"
            ),
            {"h": "a" * 64},
        )
        s.commit()
    with sync_session_factory()() as s, pytest.raises(IntegrityError):
        s.execute(
            text(
                "INSERT INTO dw_calibration_targets (id, question_hash, question, target, set_by, "
                "set_by_origin) VALUES ('ct_2', :h, 'q', 0.5, 'me', 'operator')"
            ),
            {"h": "a" * 64},
        )
        s.commit()
    with sync_session_factory()() as s, pytest.raises(IntegrityError):
        s.execute(
            text(
                "INSERT INTO dw_calibration_targets (id, question_hash, question, target, set_by, "
                "set_by_origin) VALUES ('ct_3', :h, 'q', 0.8, '', 'operator')"
            ),
            {"h": "a" * 64},
        )
        s.commit()


async def test_an_operator_target_row_with_an_approval_is_refused(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    from tests.support.calibration_fixtures import QUESTION as q

    a = (
        await client.put(
            "/api/v1/calibration-targets",
            json={"question": q, "target": 0.8},
            headers={"X-Dataworks-Agent": "agent:dataworks-mcp"},
        )
    ).json()["approval_id"]
    with sync_session_factory()() as s, pytest.raises(IntegrityError):
        s.execute(
            text(
                "INSERT INTO dw_calibration_targets (id, question_hash, question, target, set_by, "
                "set_by_origin, approval_id, approved_by) VALUES ('ct_4', :h, 'q', 0.8, 'me', "
                "'operator', :a, 'me')"
            ),
            {"h": "a" * 64, "a": a},
        )
        s.commit()


# --- 8.6 the resolver over 1,000 rows ---------------------------------------------------------


async def test_the_resolver_over_mixed_histories(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    rows = texts(1000)
    version = make_version(data_dir, rows)
    keys = [row_key(r["text"]) for r in rows]
    run = make_run(version, {k: (0.9 if i % 2 else 0.1) for i, k in enumerate(keys)})
    queue = await review_queue(client, run.id, keys[:40])
    page = await items(client, queue["id"])
    by_key = {i["row_key"]: i["id"] for i in page}
    agent = {"X-Dataworks-Agent": "agent:dataworks-mcp"}
    await decide(
        client, by_key[keys[0]], decision="override", override_label="humorous", reason="x"
    )
    await decide(client, by_key[keys[1]], decision="flag", reason="unsure")
    await client.post(
        f"/api/v1/review-items/{by_key[keys[2]]}/decisions",
        json={"decision": "flag", "reason": "agent unsure"},
        headers=agent,
    )
    await decide(
        client, by_key[keys[3]], decision="override", override_label="not_humorous", reason="x"
    )
    await decide(client, by_key[keys[3]], decision="accept")
    await client.post(
        f"/api/v1/review-items/{by_key[keys[4]]}/decisions",
        json={"decision": "accept"},
        headers=agent,
    )
    resolved = effective_label.resolve(version, None, run.id, keys)
    assert len(resolved) == 1000
    assert resolved[keys[0]].state == "overridden" and resolved[keys[0]].label == "humorous"
    assert resolved[keys[1]].state == "flagged_unresolved"
    assert resolved[keys[2]].state == "flagged_unresolved"
    assert resolved[keys[3]].state == "model" and resolved[keys[3]].label == "humorous"
    assert resolved[keys[4]].state == "model" and resolved[keys[4]].decision_id is None
    assert resolved[keys[999]].state == "model" and resolved[keys[999]].label == "humorous"
    assert resolved[keys[998]].label == "not_humorous"
    assert effective_label.RESOLVER_ID == "dw.effective-label/v1"
    assert QUESTION
