"""009 FTASKS 8.2 (FR-009.69; suite tracker 4.6): an export of a version that trained on a probe's
verdicts marks that probe as used as a training reward, naming the export.

``reward_marks.mark_export`` is called from ``export_service.run_export`` - the production export
job - and this test drives that job, so removing the call turns it red.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from src.core.config import get_settings
from src.core.database import sync_session_factory
from src.models.label_run import LabelRun
from tests.support.calibration_fixtures import make_run, make_version, row_key
from tests.support.publish_fixtures import PublishDriver, publisher

__all__ = ["publisher"]

RUBRIC = {
    "style": "binary",
    "messages": [{"role": "user", "content": "{prompt} / {completion} VERDICT: yes or no"}],
    "input_fields": ["prompt", "completion"],
    "parser": "verdict_line_v1",
    "allowed_verdicts": ["yes", "no"],
}


def probe_bound_version(data_dir: Path, mistudio_probe_id: str | None) -> str:
    rows = [{"text": f"prompt row {i}", "prompt": f"prompt row {i}"} for i in range(8)]
    plain = make_version(data_dir, rows)
    run = make_run(plain, {row_key(r["text"]): 0.9 for r in rows}, model_id="probe-host")
    with sync_session_factory()() as db:
        row = db.get(LabelRun, run.id)
        assert row is not None
        row.kind = "probe_verdict"
        identity = dict(row.labeler_identity or {})
        if mistudio_probe_id is not None:
            identity["mistudio_probe_id"] = mistudio_probe_id
        row.labeler_identity = identity
        db.commit()
    return make_version(data_dir, rows, bindings=[{"kind": "label_run", "id": run.id}])


async def bundle(
    client: httpx.AsyncClient, publisher: PublishDriver, version_id: str
) -> dict[str, Any]:
    rubric = await client.post(
        "/api/v1/rubrics", json={"name": f"r/{version_id[:8]}", "body": RUBRIC}
    )
    r = await client.post(
        "/api/v1/exports",
        json={
            "target": "reward_bundle",
            "version_id": version_id,
            "rubric_id": rubric.json()["id"],
            "judge_model_id": "judge",
            "verdict_scores": {"yes": 1.0, "no": 0.0},
        },
    )
    assert r.status_code == 202, r.text
    publisher.run_publish_tasks()
    out = (await client.get(f"/api/v1/exports/{r.json()['export_id']}")).json()
    assert out["status"] == "completed", out
    return out


@pytest.fixture
def mistudio(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(get_settings(), "mistudio_base_url", "http://mistudio.test")
    return "http://mistudio.test"


async def test_an_export_of_a_probe_trained_version_marks_the_probe_once(
    client: httpx.AsyncClient,
    operator_name: str,
    publisher: PublishDriver,
    data_dir: Path,
    mistudio: str,
) -> None:
    version_id = probe_bound_version(data_dir, "prb_42")
    first = await bundle(client, publisher, version_id)
    marks = (await client.get("/api/v1/reward-marks")).json()["items"]
    assert [(m["mistudio_probe_id"], m["source"], m["export_id"]) for m in marks] == [
        ("prb_42", "export", first["id"])
    ]
    assert marks[0]["mistudio_base_url"] == mistudio
    await bundle(client, publisher, version_id)
    assert len((await client.get("/api/v1/reward-marks")).json()["items"]) == 1


async def test_a_version_without_probe_verdicts_marks_nothing(
    client: httpx.AsyncClient,
    operator_name: str,
    publisher: PublishDriver,
    data_dir: Path,
    mistudio: str,
) -> None:
    rows = [{"text": f"p {i}", "prompt": f"p {i}"} for i in range(4)]
    await bundle(client, publisher, make_version(data_dir, rows))
    assert (await client.get("/api/v1/reward-marks")).json()["items"] == []


async def test_without_mistudio_the_export_completes_and_names_the_unmarked_probes(
    client: httpx.AsyncClient,
    operator_name: str,
    publisher: PublishDriver,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "mistudio_base_url", None)
    version_id = probe_bound_version(data_dir, "prb_7")
    out = await bundle(client, publisher, version_id)
    assert out["status"] == "completed"
    assert (await client.get("/api/v1/reward-marks")).json()["items"] == []
    from src.models import Job

    with sync_session_factory()() as db:
        job = db.get(Job, out["job_id"])
        assert job is not None and job.result is not None
        marks = job.result["reward_marks"]
    assert marks["probes"] == ["prb_7"] and marks["marked"] == []
    assert "MISTUDIO_BASE_URL" in marks["unmarked_reason"]
