"""Logs carry IDs, origin and kind, never row text, reasons or candidate payloads (006 FTASKS
13.3; FTID 006 section 12)."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
import pytest

from tests.support.calibration_fixtures import make_run, make_version, row_key

SECRET_TEXT = "the confidential headline text"
SECRET_REASON = "a private reviewer reason"


async def test_decision_and_candidate_logs_hold_no_text(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    version = make_version(data_dir, [{"text": SECRET_TEXT}, {"text": "other"}])
    run = make_run(version, {row_key(SECRET_TEXT): 0.9, row_key("other"): 0.1})
    queue = (
        await client.post(
            "/api/v1/review-queues",
            json={
                "kind": "label_review",
                "label_run_id": run.id,
                "row_keys": [row_key(SECRET_TEXT)],
            },
        )
    ).json()
    item = (await client.get(f"/api/v1/review-queues/{queue['id']}/items")).json()["items"][0]
    r = await client.post(
        f"/api/v1/review-items/{item['id']}/decisions",
        json={"decision": "flag", "reason": SECRET_REASON},
    )
    assert r.status_code == 201
    ext = (
        await client.post(
            "/api/v1/review-queues",
            json={"kind": "external", "question": "q", "label_set": ["a", "b"]},
            headers={"X-Dataworks-Agent": "agent:miforge"},
        )
    ).json()
    await client.post(
        f"/api/v1/review-queues/{ext['id']}/candidates",
        json={
            "candidates": [
                {"external_id": "c1", "prompt": SECRET_TEXT, "completion": SECRET_REASON}
            ]
        },
        headers={"X-Dataworks-Agent": "agent:miforge"},
    )
    text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert f"review.decision.recorded id={r.json()['id']} origin=operator kind=flag" in text
    assert SECRET_TEXT not in text and SECRET_REASON not in text
