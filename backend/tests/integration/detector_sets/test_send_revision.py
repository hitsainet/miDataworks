"""Feature 011 phase B: pinned, verified downloads (FTASKS 3.2 - 3.7; FR-011.3 - FR-011.9; R-P.01 -
R-P.05). The REAL send worker against the fake miStudio with and without XR-3: bodies, the
``409`` resolved by revision, the post-download commit check and ``commit_check``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import delete

from src.core.database import sync_session_factory
from src.models import DetectorSendStep
from src.models.detector_send import MiStudioRegistration
from tests.support.send_fixtures import API, SendDriver, ready_set, start_send

OTHER = "d" * 40


def downloads(sender: SendDriver) -> list[Any]:
    return sender.mistudio.calls("POST", "/api/v1/datasets/download")


def commits_by_repo(sender: SendDriver, send_id: str) -> dict[str, str]:
    steps = sender.steps(send_id)
    return {s.repo_id: s.commit for (kind, _), s in steps.items() if kind == "publish"}


def download_steps(sender: SendDriver, send_id: str) -> dict[str, DetectorSendStep]:
    return {key: s for (kind, key), s in sender.steps(send_id).items() if kind == "download"}


async def completed_send(client: httpx.AsyncClient, sender: SendDriver) -> tuple[str, str, Any]:
    set_id, versions, repos = await ready_set(client, sender)
    started = await start_send(client, set_id)
    result = sender.run(started["job_id"])
    return set_id, started["send_id"], (result, versions, repos)


# --- FR-011.4: the commit is sent only when miStudio pins downloads -----------------------------


async def test_a_pinned_send_sends_the_publish_commit_and_records_verified(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    sender.mistudio.serves_revision = True
    _, send_id, (result, _, _) = await completed_send(client, sender)
    assert result["status"] == "completed", sender.send(send_id).error
    commits = commits_by_repo(sender, send_id)
    calls = downloads(sender)
    assert len(calls) == 4
    for call in calls:
        assert set(call.body) == {"repo_id", "config", "split", "revision"}
        assert call.body["revision"] == commits[call.body["repo_id"]]
    steps = download_steps(sender, send_id)
    assert {s.commit_check for s in steps.values()} == {"verified"}
    # the recorded body is the body sent (D49), key for key
    sent = {(c.body["repo_id"], c.body["split"]): c.body for c in calls}
    assert len(sent) == 4
    for s in steps.values():
        assert s.request_body == sent[(s.request_body["repo_id"], s.request_body["split"])]
    # miStudio's rows record the commit they were downloaded at
    assert {d["_revision"] for d in sender.mistudio.datasets.values()} == set(commits.values())
    got = (await client.get(f"{API}/detector-sends/{send_id}")).json()
    download_out = [s for s in got["steps"] if s["step"] == "download"]
    assert {(s["commit_check"], s["commit_check_phrase"]) for s in download_out} == {
        ("verified", "verified by miStudio")
    }
    assert {s["commit_check"] for s in got["steps"] if s["step"] != "download"} == {None}


async def test_an_unpinned_send_sends_no_revision_and_records_claimed(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    _, send_id, (result, _, _) = await completed_send(client, sender)
    assert result["status"] == "completed"
    for call in downloads(sender):
        assert set(call.body) == {"repo_id", "config", "split"}
    steps = download_steps(sender, send_id)
    assert {s.commit_check for s in steps.values()} == {"claimed"}
    assert all("revision" not in s.request_body for s in steps.values())
    got = (await client.get(f"{API}/detector-sends/{send_id}")).json()
    assert {s["commit_check_phrase"] for s in got["steps"] if s["step"] == "download"} == {
        "claimed (miStudio does not pin downloads)"
    }


# --- FR-011.5: a 409 is resolved by revision ---------------------------------------------------


def forget_dataset_registrations() -> None:
    """miDataworks loses its record of the downloads (a new database, a pruned table): only
    miStudio's own rows remain, and only a 409 can find them."""
    with sync_session_factory()() as db:
        db.execute(delete(MiStudioRegistration).where(MiStudioRegistration.kind == "dataset"))
        db.commit()


async def resend(client: httpx.AsyncClient, sender: SendDriver, set_id: str) -> str:
    started = await start_send(client, set_id)
    sender.run(started["job_id"])
    return str(started["send_id"])


async def test_a_409_finds_mistudios_row_at_this_commit_and_reuses_it_verified(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    sender.mistudio.serves_revision = True
    set_id, first, (result, _, _) = await completed_send(client, sender)
    assert result["status"] == "completed"
    held = {
        (s.request_body["repo_id"], s.request_body["split"]): s.mistudio_dataset_id
        for s in download_steps(sender, first).values()
    }
    forget_dataset_registrations()
    second = await resend(client, sender, set_id)
    assert sender.send(second).state == "completed", sender.send(second).error
    steps = download_steps(sender, second)
    assert {s.state for s in steps.values()} == {"reused"}
    assert {s.commit_check for s in steps.values()} == {"verified"}
    assert {
        (s.request_body["repo_id"], s.request_body["split"]): s.mistudio_dataset_id
        for s in steps.values()
    } == held
    # every second-send download answered 409, and nothing new was created in miStudio
    assert len(sender.mistudio.datasets) == 4
    # the reuse is recorded again (FR-011.5 "reused and recorded")
    with sync_session_factory()() as db:
        assert db.query(MiStudioRegistration).filter_by(kind="dataset").count() == 4


async def test_a_409_with_only_other_commits_fails_naming_them(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    sender.mistudio.serves_revision = True
    set_id, versions, repos = await ready_set(client, sender)
    cal = repos[versions["cal"]]
    sender.mistudio.seed_existing_dataset(cal, "train", revision=OTHER)
    sender.mistudio.seed_existing_dataset(cal, "train")  # legacy: revision not recorded
    # miStudio answers 409 for the calibration split (scripted: a pinned 035 would only do so at
    # this commit, which it does not hold — the step must not take another commit's row)
    real = sender.mistudio._download

    def download(body: dict[str, Any]) -> httpx.Response:
        if body["repo_id"] == cal:
            return httpx.Response(409, json={"detail": "Dataset exists"})
        return real(body)

    sender.mistudio._download = download  # type: ignore[method-assign]
    started = await start_send(client, set_id)
    assert sender.run(started["job_id"])["status"] == "failed"
    send = sender.send(started["send_id"])
    assert send.error["code"] == "mistudio_dataset_exists"
    assert OTHER in send.error["message"] and "revision not recorded" in send.error["message"]
    assert sorted(send.error["details"]["commits"]) == sorted([OTHER, "revision not recorded"])
    assert sender.mistudio.count("POST", "/api/v1/probe-monitors/datasets") < 4


async def test_a_409_with_only_a_legacy_row_says_revision_not_recorded(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    sender.mistudio.serves_revision = True
    set_id, versions, repos = await ready_set(client, sender)
    cal = repos[versions["cal"]]
    sender.mistudio.seed_existing_dataset(cal, "train")
    sender.mistudio.fail("POST /datasets/download", status=409, body={"detail": "exists"})
    started = await start_send(client, set_id)
    assert sender.run(started["job_id"])["status"] == "failed"
    send = sender.send(started["send_id"])
    assert send.error["code"] == "mistudio_dataset_exists"
    assert send.error["details"]["commits"] == ["revision not recorded"]
    assert "at revision not recorded, not at the publish commit" in send.error["message"]


# --- FR-011.6: the recorded commit is checked ---------------------------------------------------


@pytest.mark.parametrize("served", [OTHER, None])
async def test_a_recorded_commit_that_differs_or_is_absent_fails_the_step(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    sender: SendDriver,
    served: str | None,
) -> None:
    sender.mistudio.serves_revision = True
    set_id, versions, repos = await ready_set(client, sender)
    ood = repos[versions["ood"]]
    sender.mistudio.revision_override[(ood, "test")] = served
    started = await start_send(client, set_id)
    assert sender.run(started["job_id"])["status"] == "failed"
    send = sender.send(started["send_id"])
    commit = commits_by_repo(sender, send.id)[ood]
    assert send.error["code"] == "mistudio_commit_mismatch"
    assert send.error["details"]["recorded"] == served
    assert send.error["details"]["commit"] == commit
    assert commit in send.error["message"]
    assert (served or "revision not recorded") in send.error["message"]
    failed = next(s for s in download_steps(sender, send.id).values() if s.state == "failed")
    assert failed.commit_check is None  # a failure never writes a check
    # the role on that split was never registered
    assert all(
        c.body["dataset_id"] != failed.mistudio_dataset_id
        for c in sender.mistudio.calls("POST", "/api/v1/probe-monitors/datasets")
    )


# --- FR-011.3, FR-011.7: read per invocation, written once --------------------------------------


async def test_a_resume_after_the_capability_appears_keeps_claimed_and_verifies_the_rest(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    set_id, versions, repos = await ready_set(client, sender)
    # fail the LAST download unit in walk order (units are walked sorted by key), so the earlier
    # ones complete unpinned
    last = sorted(
        [
            (repos[versions["train"]], "train"),
            (repos[versions["train"]], "test"),
            (repos[versions["ood"]], "test"),
            (repos[versions["cal"]], "train"),
        ]
    )[-1]
    sender.mistudio.download_errors[last] = "transient Hub error"
    started = await start_send(client, set_id)
    assert sender.run(started["job_id"])["status"] == "failed"
    before = download_steps(sender, started["send_id"])
    done_before = {k for k, s in before.items() if s.state == "done"}
    assert len(done_before) == 3
    assert {before[k].commit_check for k in done_before} == {"claimed"}
    # miStudio 035 deploys between the failure and the resume
    sender.mistudio.serves_revision = True
    del sender.mistudio.download_errors[last]
    resumed = await client.post(f"{API}/detector-sends/{started['send_id']}/resume")
    assert resumed.status_code == 202, resumed.text
    assert sender.run(resumed.json()["job_id"])["status"] == "completed"
    after = download_steps(sender, started["send_id"])
    assert {after[k].commit_check for k in done_before} == {"claimed"}
    [resumed_key] = set(after) - done_before
    assert after[resumed_key].commit_check == "verified"
    assert "revision" in after[resumed_key].request_body
    assert all("revision" not in after[k].request_body for k in done_before)


async def test_a_recorded_check_is_never_overwritten(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    from src.workers.detector_send_tasks import _write

    _, send_id, (result, _, _) = await completed_send(client, sender)
    assert result["status"] == "completed"
    with sync_session_factory()() as db:
        step = (
            db.query(DetectorSendStep)
            .filter_by(send_id=send_id, step="download")
            .order_by(DetectorSendStep.position)
            .first()
        )
        assert step is not None and step.commit_check == "claimed"
        with pytest.raises(RuntimeError, match="never changed|already recorded"):
            _write(db, step, commit_check="verified")
        db.rollback()
        _write(db, step, commit_check="claimed")  # the same value is not a change
    assert {s.commit_check for s in download_steps(sender, send_id).values()} == {"claimed"}


async def test_a_registration_reuse_is_verified_only_by_mistudios_record(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    """FTID I2: a dataset reused from an earlier send's registration is ``verified`` when miStudio
    035 records the publish commit for it, and stays a ``claimed`` legacy row when it does not."""
    set_id, first, (result, _, _) = await completed_send(client, sender)  # unpinned: legacy rows
    assert result["status"] == "completed"
    sender.mistudio.serves_revision = True
    second = await resend(client, sender, set_id)
    assert sender.send(second).state == "completed", sender.send(second).error
    steps = download_steps(sender, second)
    assert {s.state for s in steps.values()} == {"reused"}
    assert {s.commit_check for s in steps.values()} == {"claimed"}
    # once miStudio records the commit for those rows, a third send reads them as verified
    commits = commits_by_repo(sender, first)
    for d in sender.mistudio.datasets.values():
        d["_revision"] = commits[d["hf_repo_id"]]
    third = await resend(client, sender, set_id)
    assert {s.commit_check for s in download_steps(sender, third).values()} == {"verified"}


async def test_a_registration_reuse_of_another_commit_fails_the_step(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    """Control M29 survived (2026-10-18): the reused dataset's recorded commit was never compared.
    A reuse is ``verified`` only when miStudio's commit is the publish commit; another commit fails
    the step with both values, exactly as a fresh download's check does."""
    sender.mistudio.serves_revision = True
    set_id, first, (result, versions, repos) = await completed_send(client, sender)
    assert result["status"] == "completed"
    ood = repos[versions["ood"]]
    sender.mistudio.revision_override[(ood, "test")] = OTHER
    second = await resend(client, sender, set_id)
    send = sender.send(second)
    assert send.state == "failed"
    assert send.error["code"] == "mistudio_commit_mismatch"
    assert send.error["details"]["recorded"] == OTHER
    assert send.error["details"]["commit"] == commits_by_repo(sender, first)[ood]
