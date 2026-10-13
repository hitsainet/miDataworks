"""Driving a detector-set send end to end (009 FTASKS 6.x): 008's REAL publish worker against the
fake Hub, and 009's REAL send worker against the fake miStudio.

Only ``HfApi`` (``tests/support/fake_hub``) and miStudio's HTTP (``tests/support/fake_mistudio``)
are replaced. The checks (with 004's real audit), the plan, 008's builds, request digests and
authorization, the publish, the download polling and the registration all run.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest

from src.core.database import sync_session_factory
from src.models import DetectorSend, DetectorSendStep, Job
from tests.support.detector_fixtures import humor_versions, set_body
from tests.support.fake_mistudio import BASE, FakeMiStudio
from tests.support.publish_fixtures import PublishDriver, store_token

API = "/api/v1"
NAMESPACE = "mistudio"


@dataclass
class SendDriver:
    publisher: PublishDriver
    mistudio: FakeMiStudio
    emitted: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)

    def emit(self, room: str, event: str, data: dict[str, Any]) -> bool:
        self.emitted.append((room, event, data))
        return True

    def run(self, job_id: str) -> dict[str, Any]:
        from src.workers import detector_send_tasks

        result: dict[str, Any] = detector_send_tasks.run_job(job_id)
        return result

    def send_jobs(self) -> list[str]:
        """Send jobs that dispatch handed to Celery, consumed."""
        sent = [
            a[0]
            for n, a in self.publisher.builds.sent
            if n.startswith("midataworks.detector_sets.")
        ]
        self.publisher.builds.sent = [
            s
            for s in self.publisher.builds.sent
            if not s[0].startswith("midataworks.detector_sets.")
        ]
        return sent

    def send(self, send_id: str) -> DetectorSend:
        with sync_session_factory()() as db:
            row = db.get(DetectorSend, send_id)
            assert row is not None
            db.expunge(row)
            return row

    def steps(self, send_id: str) -> dict[tuple[str, str], DetectorSendStep]:
        with sync_session_factory()() as db:
            rows = db.query(DetectorSendStep).filter(DetectorSendStep.send_id == send_id).all()
            for r in rows:
                db.expunge(r)
            return {(r.step, r.unit_key): r for r in rows}

    def job(self, job_id: str) -> Job:
        return self.publisher.job(job_id)

    def seed_values(self, repos: dict[str, str], versions: dict[str, str]) -> None:
        """What miStudio will find in each downloaded split: the fixture's real label counts."""
        train = repos[versions["train"]]
        self.mistudio.values[(train, "train")] = {"humorous": 100, "not_humorous": 100}
        self.mistudio.values[(train, "test")] = {"humorous": 30, "not_humorous": 30}
        self.mistudio.values[(repos[versions["ood"]], "test")] = {
            "humorous": 60,
            "not_humorous": 60,
        }
        self.mistudio.values[(repos[versions["cal"]], "train")] = {"not_humorous": 150}


@pytest.fixture
def sender(
    monkeypatch: pytest.MonkeyPatch, publisher: PublishDriver, data_dir: Path
) -> Iterator[SendDriver]:
    from src.clients import mistudio_client
    from src.core.config import get_settings
    from src.services.detector_sets import capabilities
    from src.workers import detector_send_tasks

    fake = FakeMiStudio()
    drv = SendDriver(publisher, fake)
    monkeypatch.setattr(mistudio_client, "TRANSPORT", fake.transport())
    monkeypatch.setattr(get_settings(), "mistudio_base_url", BASE)
    monkeypatch.setattr(detector_send_tasks, "SLEEP", lambda s: None)
    monkeypatch.setattr(detector_send_tasks, "emit", drv.emit)
    monkeypatch.setattr(detector_send_tasks, "_next_jobs", lambda: None)
    capabilities.clear_cache()
    yield drv
    capabilities.clear_cache()


def repos_for(versions: dict[str, str]) -> dict[str, str]:
    from src.models import Dataset, Version

    out = {}
    with sync_session_factory()() as db:
        for vid in versions.values():
            v = db.get(Version, vid)
            assert v is not None
            ds = db.get(Dataset, v.dataset_id)
            assert ds is not None
            out[vid] = f"{NAMESPACE}/{ds.name}-v{v.number}"
    return out


async def ready_set(
    client: httpx.AsyncClient, sender: SendDriver, **fixture: Any
) -> tuple[str, dict[str, str], dict[str, str]]:
    """A created set whose versions have completed 008 builds (the first send request starts
    them and answers builds_pending; the publisher runs them)."""
    await store_token(client)
    versions = humor_versions(**fixture)
    created = await client.post(f"{API}/detector-sets", json=set_body(versions))
    assert created.status_code == 201, created.text
    set_id = created.json()["id"]
    first = await client.post(f"{API}/detector-sets/{set_id}/send", json={"namespace": NAMESPACE})
    assert first.status_code == 409, first.text
    assert first.json()["error"]["code"] == "builds_pending", first.json()
    sender.publisher.run_publish_tasks()
    repos = repos_for(versions)
    sender.seed_values(repos, versions)
    return set_id, versions, repos


async def start_send(client: httpx.AsyncClient, set_id: str, **body: Any) -> dict[str, Any]:
    payload = {"namespace": NAMESPACE, **body}
    response = await client.post(f"{API}/detector-sets/{set_id}/send", json=payload)
    assert response.status_code == 202, response.text
    data: dict[str, Any] = response.json()
    return data
