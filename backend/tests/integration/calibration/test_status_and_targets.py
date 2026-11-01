"""Status lookup, operator targets and the domain warning (006 FTASKS 7.5 – 7.7)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from src.core.database import sync_session_factory
from src.services.calibration import status_service
from tests.support.calibration_fixtures import (
    LABELS,
    MAPPING,
    QUESTION,
    Runner,
    humor_rows,
    humor_scores,
    make_run,
    make_version,
    qh,
)


async def computed(
    client: httpx.AsyncClient, runner: Runner, version: str, run_id: str, mapping: Any = None
) -> dict[str, Any]:
    sets = (await client.get(f"/api/v1/calibration-sets?version_id={version}")).json()["items"]
    if sets and mapping is None:
        set_id = sets[0]["id"]
    else:
        set_id = (
            await client.post(
                "/api/v1/calibration-sets/import",
                json={
                    "version_id": version,
                    "question": QUESTION,
                    "label_set": LABELS,
                    "mapping": mapping or MAPPING,
                },
            )
        ).json()["id"]
    job_id = (
        await client.post(
            "/api/v1/calibration-records",
            json={"label_run_id": run_id, "calibration_set_id": set_id},
        )
    ).json()["job_id"]
    rid = runner.run(job_id)["record_id"]
    return dict((await client.get(f"/api/v1/calibration-records/{rid}")).json())


async def test_status_none_recorded_then_newest_wins(
    client: httpx.AsyncClient, operator_name: str, runner: Runner, data_dir: Path
) -> None:
    rows = humor_rows(160)
    version = make_version(data_dir, rows)
    run = make_run(version, humor_scores(rows))
    none = (
        await client.get(f"/api/v1/calibration-status?labeler={run.labeler_identity_hash}")
    ).json()
    assert none == {
        "status": "none_recorded",
        "record_id": None,
        "verdict": None,
        "rule": None,
        "auroc": None,
        "calibration_set": None,
    }
    first = await computed(client, runner, version, run.id)
    second = await computed(client, runner, version, run.id)
    status = (
        await client.get(f"/api/v1/calibration-status?labeler={run.labeler_identity_hash}")
    ).json()
    assert status["status"] == "recorded" and status["record_id"] == second["id"] != first["id"]
    assert status["verdict"] == second["verdict"]["verdict"]
    assert set(status["auroc"]) == {"value", "ci_low", "ci_high", "n"}
    assert status["calibration_set"]["rows_shipped"] is False


async def test_a_fingerprint_resolves_to_its_identity(
    client: httpx.AsyncClient, operator_name: str, runner: Runner, data_dir: Path
) -> None:
    rows = humor_rows(160)
    version = make_version(data_dir, rows)
    run = make_run(version, humor_scores(rows))
    record = await computed(client, runner, version, run.id)
    by_fp = (
        await client.get(f"/api/v1/calibration-status?fingerprint={run.labeler_fingerprint}")
    ).json()
    assert by_fp["record_id"] == record["id"]
    other = make_run(version, humor_scores(rows), model_id="another-model")
    missing = (
        await client.get(f"/api/v1/calibration-status?fingerprint={other.labeler_fingerprint}")
    ).json()
    assert missing["status"] == "none_recorded"
    unknown = (await client.get("/api/v1/calibration-status?fingerprint=" + "0" * 64)).json()
    assert unknown["status"] == "none_recorded"
    with sync_session_factory()() as s:
        direct = status_service.latest(fingerprint=run.labeler_fingerprint, session=s)
    assert direct.record_id == record["id"]


async def test_status_needs_exactly_one_key(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/v1/calibration-status")).status_code == 422
    both = await client.get(
        "/api/v1/calibration-status?labeler=" + "a" * 64 + "&fingerprint=" + "b" * 64
    )
    assert both.status_code == 422


async def test_operator_targets_history_and_the_next_record_uses_them(
    client: httpx.AsyncClient, operator_name: str, runner: Runner, data_dir: Path
) -> None:
    empty = (await client.get(f"/api/v1/calibration-targets?question_hash={qh()}")).json()
    assert empty["current"] is None and empty["default_lower_bound"] == 0.70
    put = await client.put(
        "/api/v1/calibration-targets", json={"question": QUESTION, "target": 0.6}
    )
    assert put.status_code == 201, put.text
    assert put.json()["set_by"] == operator_name and put.json()["set_by_origin"] == "operator"
    again = await client.put(
        "/api/v1/calibration-targets", json={"question": QUESTION, "target": 0.65}
    )
    targets = (
        await client.get("/api/v1/calibration-targets", params={"question": QUESTION})
    ).json()
    assert targets["current"]["id"] == again.json()["id"]
    assert [t["target"] for t in targets["history"]] == [0.65, 0.6]
    rows = humor_rows(160)
    version = make_version(data_dir, rows)
    run = make_run(version, humor_scores(rows))
    record = await computed(client, runner, version, run.id)
    assert record["verdict"]["numbers"]["target"] == 0.65
    assert record["verdict"]["target_id"] == again.json()["id"]


async def test_target_range_is_enforced(client: httpx.AsyncClient, operator_name: str) -> None:
    for bad in (0.5, 1.0, 0.2):
        r = await client.put(
            "/api/v1/calibration-targets", json={"question": QUESTION, "target": bad}
        )
        assert r.status_code == 422


async def test_a_domain_warning_appears_and_the_verdict_is_unchanged(
    client: httpx.AsyncClient, operator_name: str, runner: Runner, data_dir: Path
) -> None:
    rows = humor_rows(160)
    version = make_version(data_dir, rows)
    run = make_run(version, humor_scores(rows))
    before = await computed(client, runner, version, run.id)
    assert not [w for w in before["warnings"] if w["kind"] == "calibration_domain"]
    long_rows = [{**r, "text": (r["text"] + " ") * 40} for r in rows]
    long_version = make_version(data_dir, long_rows)
    make_run(long_version, dict.fromkeys(humor_scores(long_rows), 0.5))  # same labeler identity
    after = await computed(client, runner, version, run.id)
    warnings = [w for w in after["warnings"] if w["kind"] == "calibration_domain"]
    assert len(warnings) == 1 and warnings[0]["version_id"] == long_version
    assert warnings[0]["labeled"]["median"] > warnings[0]["calibration"]["median"]
    assert after["verdict"]["verdict"] == before["verdict"]["verdict"]
    assert after["metrics_sha256"] == before["metrics_sha256"]
