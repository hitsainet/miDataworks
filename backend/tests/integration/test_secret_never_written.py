"""Stored secrets never reach a row, a result, a file or a log (ADR-015; task 8.6).

The self-test job runs with a known Hugging Face token and endpoint keys configured, and with
``check_hf_token`` so the worker really decrypts the token. Then every ``dw_*`` table (discovered
from the live metadata, so feature 005's label-run and lease tables are covered the day they are
added), the job result, every file under ``DATA_DIR`` and every captured log record are searched
for each secret value.

Mutation controls: put the token into the job result; write it to a file under DATA_DIR; log it
with redaction disabled. Each must turn this file red.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from src.core.database import Base, get_sync_engine
from src.workers import selftest_tasks

HF_TOKEN = "hf_NeverWriteMeAnywhere_0123456789abcdef"
JUDGE_KEY = "sk-judge-NeverWriteMe-0123456789"
CLASSIFIER_KEY = "tei-key-NeverWriteMe-0123456789"
SECRETS = (HF_TOKEN, JUDGE_KEY, CLASSIFIER_KEY)


def _all_rows_as_text() -> str:
    chunks: list[str] = []
    with get_sync_engine().connect() as conn:
        for table in Base.metadata.sorted_tables:
            rows = conn.execute(text(f"SELECT t::text FROM {table.name} AS t")).scalars()
            chunks.extend(rows)
    return "\n".join(chunks)


def _all_files_as_bytes(root: Path) -> bytes:
    return b"\n".join(p.read_bytes() for p in root.rglob("*") if p.is_file())


async def test_secrets_appear_nowhere_after_a_job_runs(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caplog.set_level(logging.DEBUG)
    emitted: list[dict[str, Any]] = []
    monkeypatch.setattr(selftest_tasks, "emit", lambda room, event, data: emitted.append(data))
    from src.core.celery_app import celery_app

    monkeypatch.setattr(celery_app, "send_task", lambda *a, **k: None)

    assert (
        await client.put("/api/v1/settings/hf_token", json={"value": HF_TOKEN})
    ).status_code == 200
    await client.put(
        "/api/v1/endpoint-roles/judge",
        json={"protocol": "openai_chat", "base_url": "http://judge.test/v1", "api_key": JUDGE_KEY},
    )
    await client.put(
        "/api/v1/endpoint-roles/classifier",
        json={
            "protocol": "tei_classification",
            "base_url": "http://tei.test",
            "api_key": CLASSIFIER_KEY,
        },
    )
    created = await client.post(
        "/api/v1/jobs/selftest",
        json={"duration_seconds": 0.3, "step_seconds": 0.1, "rows": 50, "check_hf_token": True},
    )
    job_id = created.json()["id"]

    result = selftest_tasks.run_selftest.run(job_id)  # the task body, in this process
    assert result["status"] == "completed"
    assert result["hf_token_configured"] is True, "the worker must really have decrypted the token"

    job = (await client.get(f"/api/v1/jobs/{job_id}")).json()
    rows = _all_rows_as_text()
    files = _all_files_as_bytes(data_dir)
    responses = (await client.get("/api/v1/settings")).text + (
        await client.get("/api/v1/endpoint-roles")
    ).text
    for secret in SECRETS:
        assert secret not in rows, f"{secret[:6]}… is stored in clear in a dw_* row"
        assert secret not in str(job), f"{secret[:6]}… is in the job record or result"
        assert secret not in str(result), f"{secret[:6]}… is in the task result"
        assert secret.encode() not in files, f"{secret[:6]}… is in a file under DATA_DIR"
        assert secret not in caplog.text, f"{secret[:6]}… reached a log record"
        assert secret not in str(emitted), f"{secret[:6]}… was emitted over the socket"
        assert secret not in responses, f"{secret[:6]}… was returned by the API"
    assert job["status"] == "completed"
