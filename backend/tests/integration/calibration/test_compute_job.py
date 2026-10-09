"""Calibration records: start validation and the job with its real task body (006 FTASKS 7.1 –
7.4, 4.5)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select, update

from src.core.cancellation import OperatorCancelled
from src.core.database import sync_session_factory
from src.models.calibration import CalibrationCheck, CalibrationRecord, CalibrationVerdict
from src.models.decision_template import DecisionTemplate
from src.models.label_run import LabelRun
from tests.support.calibration_fixtures import (
    LABELS,
    MAPPING,
    QUESTION,
    Runner,
    humor_rows,
    humor_scores,
    make_run,
    make_version,
)


async def make_set(
    client: httpx.AsyncClient, version: str, mapping: dict[str, Any] | None = None
) -> str:
    response = await client.post(
        "/api/v1/calibration-sets/import",
        json={
            "version_id": version,
            "question": QUESTION,
            "label_set": LABELS,
            "mapping": mapping or MAPPING,
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def record_count() -> int:
    with sync_session_factory()() as s:
        return int(s.execute(select(func.count()).select_from(CalibrationRecord)).scalar_one())


@pytest.fixture
def humor(data_dir: Path) -> tuple[str, list[dict[str, Any]]]:
    rows = humor_rows(240)
    return make_version(data_dir, rows), rows


async def start(client: httpx.AsyncClient, run_id: str, set_id: str) -> httpx.Response:
    return await client.post(
        "/api/v1/calibration-records", json={"label_run_id": run_id, "calibration_set_id": set_id}
    )


async def test_a_record_is_computed_with_checks_and_verdict(
    client: httpx.AsyncClient, operator_name: str, runner: Runner, humor: Any
) -> None:
    version, rows = humor
    set_id = await make_set(client, version)
    run = make_run(version, humor_scores(rows))
    started = await start(client, run.id, set_id)
    assert started.status_code == 202, started.text
    job_id = started.json()["job_id"]
    assert started.json()["room"] == f"dataworks/calibration/{job_id}"
    result = runner.run(job_id)
    assert result["status"] == "completed", result
    assert runner.job(job_id).status == "completed"
    record = (await client.get(f"/api/v1/calibration-records/{result['record_id']}")).json()
    metrics = record["metrics"]
    assert 0.5 < metrics["auroc"]["value"] < 1.0
    assert metrics["auroc"]["n"] == metrics["auroc"]["n_pos"] + metrics["auroc"]["n_neg"]
    assert metrics["ceiling"] is not None and metrics["comparison_auroc"] is not None
    assert metrics["paired"]["pairs"] > 0 and metrics["paired"]["groups"] > 0
    assert metrics["reference_diagnostic"]["n_neg"] > 0
    assert len(metrics["reliability"]) == 10
    assert metrics["band_shares"]["threshold_positive"] == 0.8
    assert record["settings"]["seeds"]["auroc"] == 20261004
    assert record["verdict"]["verdict"] in ("passes", "fails")
    checks = {(c["metric_id"], c["check_id"]): c for c in record["checks"]}
    assert checks[("auroc", "row_alignment")]["result"] == "pass"
    assert checks[("auroc", "shortcut_comparison")]["result"] == "not_applicable"
    assert ("ceiling", "ceiling_circularity") in checks
    assert ("reference_diagnostic", "negative_class_control") in checks
    assert record["labeler_identity_hash"] == run.labeler_identity_hash
    assert any(e[1] == "calibration:completed" for e in runner.emitted)


async def test_recomputing_the_same_inputs_gives_the_same_metrics_hash(
    client: httpx.AsyncClient, operator_name: str, runner: Runner, humor: Any
) -> None:
    version, rows = humor
    set_id = await make_set(client, version)
    run = make_run(version, humor_scores(rows))
    hashes = []
    for _ in range(2):
        job_id = (await start(client, run.id, set_id)).json()["job_id"]
        rid = runner.run(job_id)["record_id"]
        with sync_session_factory()() as s:
            hashes.append(s.get(CalibrationRecord, rid).metrics_sha256)  # type: ignore[union-attr]
    assert hashes[0] == hashes[1] and record_count() == 2


async def test_a_cancel_mid_run_writes_nothing(
    client: httpx.AsyncClient,
    operator_name: str,
    runner: Runner,
    humor: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version, rows = humor
    set_id = await make_set(client, version)
    run = make_run(version, humor_scores(rows))
    job_id = (await start(client, run.id, set_id)).json()["job_id"]
    from src.services.calibration import record_service

    real = record_service.compute_figures

    def cancel_then_compute(*args: Any, **kwargs: Any) -> Any:
        raise OperatorCancelled(job_id, "cancelled")

    monkeypatch.setattr(record_service, "compute_figures", cancel_then_compute)
    out = runner.run(job_id)
    assert out["status"] == "cancelled"
    assert runner.job(job_id).status == "cancelled"
    assert record_count() == 0
    assert real is not None


async def test_a_failure_writes_nothing(
    client: httpx.AsyncClient,
    operator_name: str,
    runner: Runner,
    humor: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version, rows = humor
    set_id = await make_set(client, version)
    run = make_run(version, humor_scores(rows))
    job_id = (await start(client, run.id, set_id)).json()["job_id"]
    from src.services.calibration import record_service

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(record_service, "decide", boom)
    assert runner.run(job_id)["status"] == "failed"
    job = runner.job(job_id)
    assert job.status == "failed" and "disk on fire" in (job.error or "")
    assert record_count() == 0
    with sync_session_factory()() as s:
        assert s.execute(select(func.count()).select_from(CalibrationCheck)).scalar_one() == 0
        assert s.execute(select(func.count()).select_from(CalibrationVerdict)).scalar_one() == 0


async def test_cancelling_through_the_jobs_route_stops_the_job(
    client: httpx.AsyncClient, operator_name: str, runner: Runner, humor: Any
) -> None:
    version, rows = humor
    set_id = await make_set(client, version)
    run = make_run(version, humor_scores(rows))
    job_id = (await start(client, run.id, set_id)).json()["job_id"]
    cancelled = await client.post(f"/api/v1/jobs/{job_id}/cancel", json={"reason": "stop"})
    assert cancelled.status_code < 300, cancelled.text
    assert cancelled.json()["job"]["status"] == "cancelled"
    assert runner.run(job_id)["status"] == "skipped"
    assert record_count() == 0


# --- 7.1 refusals before queueing -------------------------------------------------------------


async def test_run_not_completed(client: httpx.AsyncClient, operator_name: str, humor: Any) -> None:
    version, rows = humor
    set_id = await make_set(client, version)
    run = make_run(version, humor_scores(rows), state="running")
    r = await start(client, run.id, set_id)
    assert r.status_code == 409 and r.json()["error"]["code"] == "RUN_NOT_COMPLETED"


async def test_run_version_mismatch(
    client: httpx.AsyncClient, operator_name: str, humor: Any, data_dir: Path
) -> None:
    version, rows = humor
    set_id = await make_set(client, version)
    other = make_version(data_dir, rows)
    run = make_run(other, humor_scores(rows))
    r = await start(client, run.id, set_id)
    assert r.status_code == 409 and r.json()["error"]["code"] == "RUN_VERSION_MISMATCH"


async def test_question_mismatch(client: httpx.AsyncClient, operator_name: str, humor: Any) -> None:
    version, rows = humor
    set_id = await make_set(client, version)
    run = make_run(version, humor_scores(rows), question="Is this text funny?")
    r = await start(client, run.id, set_id)
    assert r.status_code == 409 and r.json()["error"]["code"] == "QUESTION_MISMATCH"


async def test_rows_not_scored_names_the_count(
    client: httpx.AsyncClient, operator_name: str, humor: Any
) -> None:
    version, rows = humor
    set_id = await make_set(client, version)
    scores = humor_scores(rows)
    from tests.support.calibration_fixtures import row_key

    labeled = [
        row_key(r["text"])
        for r in rows
        if r["meanGrade"] is not None and (r["meanGrade"] >= 1.6 or r["meanGrade"] <= 0.4)
    ]
    for key in labeled[:7]:
        del scores[key]
    run = make_run(version, scores)
    r = await start(client, run.id, set_id)
    assert r.status_code == 409
    error = r.json()["error"]
    assert error["code"] == "ROWS_NOT_SCORED" and error["details"]["missing"] == 7
    assert error["message"].startswith("7 calibration rows have no label in this run.")


async def test_a_skipped_row_without_probability_counts_as_unscored(
    client: httpx.AsyncClient, operator_name: str, humor: Any
) -> None:
    version, rows = humor
    set_id = await make_set(client, version)
    scores: dict[str, float | None] = dict(humor_scores(rows))
    from tests.support.calibration_fixtures import row_key

    first = next(
        row_key(r["text"]) for r in rows if r["meanGrade"] is not None and r["meanGrade"] >= 1.6
    )
    scores[first] = None
    run = make_run(version, scores)
    r = await start(client, run.id, set_id)
    assert r.status_code == 409 and r.json()["error"]["details"]["missing"] == 1


# --- edge cases -------------------------------------------------------------------------------


async def test_discrete_labeler_scores_and_says_so(
    client: httpx.AsyncClient, operator_name: str, runner: Runner, humor: Any
) -> None:
    version, rows = humor
    set_id = await make_set(client, version)
    scores = humor_scores(rows)
    outcomes = {k: ("positive" if p > 0.3 else "negative") for k, p in scores.items()}
    run = make_run(version, dict.fromkeys(scores), outcomes=outcomes, thresholds=None)
    job_id = (await start(client, run.id, set_id)).json()["job_id"]
    record = (
        await client.get(f"/api/v1/calibration-records/{runner.run(job_id)['record_id']}")
    ).json()
    assert record["score_kind"] == "discrete"
    assert record["metrics"]["reliability"] is None
    assert record["metrics"]["reasons"]["reliability"] == "score is discrete"
    assert record["metrics"]["auroc"] is not None


async def test_no_group_column_says_not_available(
    client: httpx.AsyncClient, operator_name: str, runner: Runner, humor: Any
) -> None:
    version, rows = humor
    mapping = {k: v for k, v in MAPPING.items() if k not in ("group", "reference", "strata")}
    set_id = await make_set(client, version, mapping)
    run = make_run(version, humor_scores(rows))
    job_id = (await start(client, run.id, set_id)).json()["job_id"]
    record = (
        await client.get(f"/api/v1/calibration-records/{runner.run(job_id)['record_id']}")
    ).json()
    assert record["metrics"]["paired"] is None
    assert record["metrics"]["reasons"]["paired"] == "Not available: no group column"


async def test_conformance_is_shown_only_when_the_template_records_it(
    client: httpx.AsyncClient, operator_name: str, runner: Runner, humor: Any
) -> None:
    version, rows = humor
    set_id = await make_set(client, version)
    run = make_run(version, humor_scores(rows))
    job_id = (await start(client, run.id, set_id)).json()["job_id"]
    plain = (
        await client.get(f"/api/v1/calibration-records/{runner.run(job_id)['record_id']}")
    ).json()
    assert plain["conformance"] is None

    with sync_session_factory()() as s:
        template = DecisionTemplate(
            id="dt_conformance1",
            name="bare",
            version=1,
            content_hash="c" * 64,
            body={"conformance": {"cases": 102, "within": 0.01, "passed": 102}},
            protocol="openai_scoring",
            created_by="Test Operator",
            created_by_origin="operator",
        )
        s.add(template)
        s.flush()
        s.execute(update(LabelRun).where(LabelRun.id == run.id).values(template_id=template.id))
        s.commit()
    job_id = (await start(client, run.id, set_id)).json()["job_id"]
    shown = (
        await client.get(f"/api/v1/calibration-records/{runner.run(job_id)['record_id']}")
    ).json()
    assert (
        shown["conformance"]["passed"] == 102
        and shown["conformance"]["template_id"] == "dt_conformance1"
    )
