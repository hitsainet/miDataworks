"""Keys and lease IDs absent from rows, results, files and logs (005 FTASKS 13.4, 10.7, 15.3).

Extends Foundation's test_secret_never_written.py to a label run, a sample, a keep-share preview
and a lease cycle: every dw_* table (from the live metadata), every file under DATA_DIR and every
captured log record are searched for the endpoint key and every lease ID miLLM handed out.
"""

from __future__ import annotations

import logging

import httpx
import pytest

from tests.integration.labeling.helpers import QUESTION, setup_classifier, start_body
from tests.integration.test_secret_never_written import _all_files_as_bytes, _all_rows_as_text
from tests.support.labeling_fixtures import KEY, Labeling


async def test_no_key_or_lease_id_anywhere(
    client: httpx.AsyncClient, labeling: Labeling, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    version_id, template_id = await setup_classifier(client, labeling, n=30)
    run = (await client.post("/api/v1/label-runs", json=start_body(version_id, template_id))).json()
    labeling.run_until_done(run["id"])
    sample = await client.post(
        "/api/v1/labeling/sample",
        json={
            "input_version_id": version_id,
            "role": "classifier",
            "template_id": template_id,
            "question": QUESTION,
            "field_map": {"text": "text"},
            "rows": 3,
        },
    )
    preview = await client.post(
        "/api/v1/labeling/keep-share",
        json={
            "input_version_id": version_id,
            "template_id": template_id,
            "question": QUESTION,
            "field_map": {"text": "text"},
            "threshold_positive": 0.5,
            "threshold_negative": 0.2,
            "sample_rows": 10,
        },
    )
    from src.workers import label_run_tasks

    label_run_tasks.preview_job(preview.json()["job_id"])
    lease_ids = [
        r.headers["x-millm-lease"] for r in labeling.millm.requests if "x-millm-lease" in r.headers
    ]
    assert lease_ids, "the run held a lease"
    secrets = [KEY, *set(lease_ids)]
    rows = _all_rows_as_text()
    files = _all_files_as_bytes(labeling.data_dir)
    responses = (
        sample.text + preview.text + (await client.get(f"/api/v1/label-runs/{run['id']}")).text
    )
    for secret in secrets:
        assert secret not in rows
        assert secret.encode() not in files
        assert secret not in caplog.text
        assert secret not in responses
    assert any("label_run.chunk_committed" in r.getMessage() for r in caplog.records)
    assert any("label_run.lease_joined" in r.getMessage() for r in caplog.records)
