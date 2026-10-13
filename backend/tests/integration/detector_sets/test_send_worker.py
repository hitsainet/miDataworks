"""The send end to end: checks, 008's builds and publish, miStudio's download and registration
(FTASKS 6.1 - 6.9; FR-009.17 - FR-009.29). Payload AND call count are asserted per route."""

from __future__ import annotations

from pathlib import Path

import httpx

from src.core.database import sync_session_factory
from src.models import Publish
from tests.support.detector_fixtures import humor_versions
from tests.support.publish_fixtures import TOKEN
from tests.support.send_fixtures import API, SendDriver, ready_set, start_send


async def test_an_operator_send_publishes_downloads_registers_and_records(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    set_id, versions, repos = await ready_set(client, sender)
    started = await start_send(client, set_id)
    [job_id] = sender.send_jobs()
    assert job_id == started["job_id"]
    assert sender.run(job_id)["status"] == "completed"

    send = sender.send(started["send_id"])
    assert send.state == "completed"
    steps = sender.steps(send.id)
    assert {s.state for s in steps.values()} == {"done"}
    # 008: one publish per distinct version, carrying the send ID (FTASKS 11.4)
    with sync_session_factory()() as db:
        pubs = db.query(Publish).filter(Publish.send_id == send.id).all()
        assert sorted(p.repo_id for p in pubs) == sorted(set(repos.values()))
        assert {p.status for p in pubs} == {"published"}
        assert {p.requested_visibility for p in pubs} == {"private"}
    # miStudio: one download per (repository, split), exactly three keys, no token
    downloads = sender.mistudio.calls("POST", "/api/v1/datasets/download")
    assert len(downloads) == 4
    for call in downloads:
        assert set(call.body) == {"repo_id", "config", "split"}
    assert sorted((c.body["repo_id"], c.body["split"]) for c in downloads) == sorted(
        [
            (repos[versions["train"]], "train"),
            (repos[versions["train"]], "test"),
            (repos[versions["ood"]], "test"),
            (repos[versions["cal"]], "train"),
        ]
    )
    # one registration per role, with the pinned key set and the role table (FR-009.4)
    regs = sender.mistudio.calls("POST", "/api/v1/probe-monitors/datasets")
    assert len(regs) == 4
    by_role = {c.body["role"] + "/" + str(c.body.get("distribution")): c.body for c in regs}
    assert set(by_role) == {
        "train/None",
        "eval/in_distribution",
        "eval/out_of_distribution",
        "calibration/None",
    }
    assert by_role["eval/out_of_distribution"]["pair_column"] == "pair_id"
    assert all("keyword_filter" not in c.body for c in regs)
    # registered counts were compared and recorded
    reg_steps = [s for (kind, _), s in steps.items() if kind == "register"]
    assert all(
        s.registered_counts
        == {k: s.expected_counts[k] for k in ("positive", "negative", "excluded")}
        for s in reg_steps
    )
    # the run-request skeleton, in ProbeRunCreate shape (FR-009.29)
    got = (await client.get(f"{API}/detector-sends/{send.id}")).json()
    skeleton = got["run_request"]
    assert set(skeleton) == {"train_dataset_id", "eval_dataset_ids", "calibration_dataset_id"}
    assert len(skeleton["eval_dataset_ids"]) == 2
    views = sender.mistudio.views
    assert views[skeleton["eval_dataset_ids"][0]]["distribution"] == "in_distribution"
    assert views[skeleton["calibration_dataset_id"]]["role"] == "calibration"


async def test_no_secret_reaches_mistudio(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    """FR-009.22; R-03.44: no access_token, Authorization header or HF token value."""
    set_id, _, _ = await ready_set(client, sender)
    started = await start_send(client, set_id)
    sender.run(started["job_id"])
    assert sender.mistudio.requests
    for call in sender.mistudio.requests:
        assert "authorization" not in call.headers
        assert (
            TOKEN not in repr(call.body)
            and TOKEN not in repr(call.headers)
            and TOKEN not in str(call.query)
        )
        if isinstance(call.body, dict):
            assert "access_token" not in call.body


async def test_a_refused_check_refuses_the_send_with_its_checks(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    from tests.support.detector_fixtures import set_body

    versions = humor_versions(shortcut=True)
    created = (await client.post(f"{API}/detector-sets", json=set_body(versions))).json()
    response = await client.post(
        f"{API}/detector-sets/{created['id']}/send", json={"namespace": "mistudio"}
    )
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "send_refused" and error["message"].startswith("D-3")
    assert {c["code"] for c in error["details"]["checks"]} >= {"D-1", "D-3", "D-8"}
    assert sender.mistudio.requests == []


async def test_a_counts_mismatch_fails_the_role_with_both_counts(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    set_id, versions, repos = await ready_set(client, sender)
    sender.mistudio.wrong_counts[(repos[versions["ood"]], "test")] = {
        "positive": 59,
        "negative": 60,
        "excluded": 0,
    }
    started = await start_send(client, set_id)
    assert sender.run(started["job_id"])["status"] == "failed"
    send = sender.send(started["send_id"])
    assert send.state == "failed" and send.error["code"] == "counts_mismatch"
    assert send.error["details"]["registered"]["positive"] == 59
    assert send.error["details"]["expected"]["positive"] == 60
    assert sender.job(started["job_id"]).status == "failed"


async def test_an_excluded_count_mismatch_fails_the_role(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    """2026-10-07 defect 3: a null label miStudio reads differently shows only in excluded."""
    set_id, versions, repos = await ready_set(client, sender)
    sender.mistudio.wrong_counts[(repos[versions["ood"]], "test")] = {
        "positive": 60,
        "negative": 60,
        "excluded": 7,
    }
    started = await start_send(client, set_id)
    assert sender.run(started["job_id"])["status"] == "failed"
    send = sender.send(started["send_id"])
    assert send.state == "failed" and send.error["code"] == "counts_mismatch"
    assert send.error["details"]["registered"]["excluded"] == 7
    assert send.error["details"]["expected"]["excluded"] == 0
    assert "7 excluded" in send.error["message"]


async def test_a_registration_without_an_excluded_count_compares_the_two_classes(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    """Excluded is compared WHERE miStudio reports it; an older response without it still sends."""
    set_id, versions, repos = await ready_set(client, sender)
    sender.mistudio.wrong_counts[(repos[versions["ood"]], "test")] = {
        "positive": 60,
        "negative": 60,
    }
    started = await start_send(client, set_id)
    sender.run(started["job_id"])
    send = sender.send(started["send_id"])
    assert send.state == "completed", send.error
    assert send.error is None


async def test_a_409_on_download_is_never_silently_reused(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    set_id, versions, repos = await ready_set(client, sender)
    existing = sender.mistudio.seed_existing_dataset(repos[versions["cal"]], "train")
    started = await start_send(client, set_id)
    assert sender.run(started["job_id"])["status"] == "failed"
    send = sender.send(started["send_id"])
    assert send.error["code"] == "mistudio_dataset_exists"
    assert send.error["details"]["mistudio_dataset_id"] == existing
    assert sender.mistudio.count("POST", "/api/v1/probe-monitors/datasets") < 4


async def test_a_download_error_names_the_token_next_step(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    set_id, versions, repos = await ready_set(client, sender)
    sender.mistudio.download_errors[(repos[versions["ood"]], "test")] = "401 Unauthorized"
    started = await start_send(client, set_id)
    assert sender.run(started["job_id"])["status"] == "failed"
    send = sender.send(started["send_id"])
    assert send.error["code"] == "mistudio_download_failed"
    assert "401 Unauthorized" in send.error["message"]
    assert "Hugging Face token" in send.error["next_step"]


async def test_mistudio_unreachable_and_not_json_fail_distinctly(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    set_id, _, _ = await ready_set(client, sender)
    sender.mistudio.fail("GET /api/openapi.json", non_json=True)
    started = await start_send(client, set_id)
    sender.run(started["job_id"])
    assert sender.send(started["send_id"]).error["code"] == "mistudio_not_json"
    sender.mistudio.fail("POST /datasets/download", drop=True)
    resumed = await client.post(f"{API}/detector-sends/{started['send_id']}/resume")
    assert resumed.status_code == 202, resumed.text
    sender.run(resumed.json()["job_id"])
    assert sender.send(started["send_id"]).error["code"] == "mistudio_unreachable"


async def test_a_second_send_of_a_running_set_is_refused(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    set_id, _, _ = await ready_set(client, sender)
    await start_send(client, set_id)
    again = await client.post(f"{API}/detector-sets/{set_id}/send", json={"namespace": "mistudio"})
    assert again.status_code == 409 and again.json()["error"]["code"] == "send_in_progress"


async def test_every_download_poll_writes_a_heartbeat(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    sender: SendDriver,
    monkeypatch: object,
) -> None:
    """Control C16 survived: the janitor needs a database heartbeat on EVERY poll of a download
    (miStudio memory "long phases need a DB heartbeat"), not only at step boundaries."""
    from src.workers import detector_send_tasks

    set_id, _, _ = await ready_set(client, sender)
    sender.mistudio.polls_until_ready = 3
    beats: list[str] = []
    original = detector_send_tasks.record_progress

    def counting(job_id: str, **kwargs: object) -> bool:
        if str(kwargs.get("message", "")).startswith("miStudio download"):
            beats.append(str(kwargs["message"]))
        return original(job_id, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(detector_send_tasks, "record_progress", counting)  # type: ignore[attr-defined]
    started = await start_send(client, set_id)
    assert sender.run(started["job_id"])["status"] == "completed"
    polls = sender.mistudio.count("GET", "/api/v1/datasets/")
    assert polls == 4 * 3
    assert len(beats) == polls
