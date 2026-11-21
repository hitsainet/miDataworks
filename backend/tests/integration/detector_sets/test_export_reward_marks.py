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
from src.models.version import VersionInput
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
    assert "MIWORKBENCH_BASE_URL" in marks["unmarked_reason"]


def child_of(data_dir: Path, parent: str, rows: list[dict[str, Any]], **kwargs: Any) -> str:
    child = make_version(data_dir, rows, **kwargs)
    with sync_session_factory()() as db:
        db.add(VersionInput(version_id=child, position=0, kind="version", input_version_id=parent))
        db.commit()
    return child


async def test_an_export_of_a_repair_build_marks_the_probe_its_ancestor_bound(
    client: httpx.AsyncClient,
    operator_name: str,
    publisher: PublishDriver,
    data_dir: Path,
    mistudio: str,
) -> None:
    """A repair or re-split binds nothing and still carries the verdicts: a probe that goes unmarked
    reads as clean while it is a training signal."""
    labelled = probe_bound_version(data_dir, "prb_lineage")
    rows = [{"text": f"prompt row {i}", "prompt": f"prompt row {i}"} for i in range(8)]
    repaired = child_of(data_dir, labelled, rows)
    done = await bundle(client, publisher, repaired)
    marks = (await client.get("/api/v1/reward-marks")).json()["items"]
    assert [(m["mistudio_probe_id"], m["export_id"]) for m in marks] == [
        ("prb_lineage", done["id"])
    ]


async def test_a_relabel_still_marks_the_probe_behind_it(
    client: httpx.AsyncClient,
    operator_name: str,
    publisher: PublishDriver,
    data_dir: Path,
    mistudio: str,
) -> None:
    """Unlike the effective label, the mark reads the WHOLE lineage: a later relabel cannot undo
    what was filtered on the probe's verdicts."""
    labelled = probe_bound_version(data_dir, "prb_behind")
    rows = [{"text": f"prompt row {i}", "prompt": f"prompt row {i}"} for i in range(8)]
    other = make_run(labelled, {row_key(r["text"]): 0.5 for r in rows}, model_id="plain-classifier")
    relabelled = child_of(
        data_dir, labelled, rows, bindings=[{"kind": "label_run", "id": other.id}]
    )
    await bundle(client, publisher, relabelled)
    marks = (await client.get("/api/v1/reward-marks")).json()["items"]
    assert [m["mistudio_probe_id"] for m in marks] == ["prb_behind"]
