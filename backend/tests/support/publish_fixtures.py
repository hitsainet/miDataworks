"""Fixtures for feature 008's integration tests.

A version is built for real (002's orchestrator over the stub operators, a ``stub_split`` that
declares a held-out ``test`` split), then the publish worker's REAL task bodies run in-process
against :class:`tests.support.fake_hub.FakeHub`. Only ``HfApi`` is replaced; the ``HubClient``,
the token decryption, the checks, the card, the manifest, the plan and the verification all run.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest

from src.core.database import sync_session_factory
from src.models import Job
from tests.support.fake_hub import FakeHub
from tests.support.stub_operators import body
from tests.support.version_fixtures import HUMOR_TEST, HUMOR_TRAIN, BuildDriver, make_source

TOKEN = "hf_008TestToken0123456789abcdefWRITE"
REPO = "mistudio/humor-test"


@dataclass
class PublishDriver:
    hub: FakeHub
    builds: BuildDriver
    sent: list[tuple[str, list[Any]]] = field(default_factory=list)
    emitted: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)

    def send_task(self, name: str, args: list[Any] | None = None, **_: Any) -> None:
        self.sent.append((name, list(args or [])))

    def emit(self, room: str, event: str, data: dict[str, Any]) -> bool:
        self.emitted.append((room, event, data))
        return True

    def job(self, job_id: str) -> Job:
        with sync_session_factory()() as db:
            row = db.get(Job, job_id)
            assert row is not None
            db.expunge(row)
            return row

    def run_publish_tasks(self) -> list[str]:
        """Run every queued publish-queue job that dispatch sent, in order, until none remain."""
        from src.workers import publish_tasks

        runners = {
            "midataworks.publish.build": publish_tasks.run_build_job,
            "midataworks.publish.check": publish_tasks.run_check_job,
            "midataworks.publish.publish": publish_tasks.run_publish_job,
            "midataworks.publish.reverify": publish_tasks.run_reverify_job,
            "midataworks.publish.export": publish_tasks.run_export_job,
        }
        ran: list[str] = []
        while True:
            pending = [(n, a) for n, a in self.sent if n in runners]
            self.sent = [(n, a) for n, a in self.sent if n not in runners]
            if not pending:
                return ran
            for name, args in pending:
                runners[name](args[0])
                ran.append(args[0])


@pytest.fixture
def publisher(
    monkeypatch: pytest.MonkeyPatch, driver: BuildDriver, data_dir: Path
) -> Iterator[PublishDriver]:
    from src.core.celery_app import celery_app
    from src.workers import publish_tasks

    hub = FakeHub()
    hub.add_token(TOKEN)
    drv = PublishDriver(hub, driver)
    original = celery_app.send_task

    def send(name: str, args: list[Any] | None = None, **kwargs: Any) -> Any:
        if name.startswith("midataworks.publish."):
            return drv.send_task(name, args, **kwargs)
        return driver.send_task(name, args, **kwargs)

    monkeypatch.setattr(celery_app, "send_task", send)
    monkeypatch.setattr(publish_tasks, "emit", drv.emit)
    monkeypatch.setattr(publish_tasks, "HUB_API_FACTORY", hub.api)
    monkeypatch.setattr(publish_tasks, "_next_jobs", lambda: None)
    yield drv
    assert original is not None


async def store_token(client: httpx.AsyncClient, token: str = TOKEN) -> None:
    response = await client.put("/api/v1/settings/hf_token", json={"value": token})
    assert response.status_code == 200, response.text


async def built_version(
    client: httpx.AsyncClient,
    driver: BuildDriver,
    data_dir: Path,
    *,
    name: str = "humor",
    licence: Any = "cc-by-2.0",
    held_out: bool = True,
) -> str:
    """A completed version of a real build: ``train`` and (by default) a held-out ``test``."""
    source_id = make_source(
        data_dir, {"train": HUMOR_TRAIN, "test": HUMOR_TEST}, repo_id=f"org/{name}", licence=licence
    )
    ds = await client.post("/api/v1/datasets", json={"name": name, "target_type": "detector"})
    assert ds.status_code == 201, ds.text
    steps = [("stub_split", {"held_out": "test", "count": 3})] if held_out else [("stub_keep", {})]
    rec = await client.post("/api/v1/recipes", json={"name": f"{name}-r", "body": body(*steps)})
    assert rec.status_code == 201, rec.text
    response = await client.post(
        "/api/v1/versions",
        json={
            "dataset_id": ds.json()["id"],
            "recipe_revision_id": rec.json()["head_revision_id"],
            "inputs": [{"kind": "source", "source_id": source_id}],
        },
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    assert driver.run(job_id) == "completed"
    with sync_session_factory()() as db:
        job = db.get(Job, job_id)
        assert job is not None and job.result is not None
        return str(job.result["version_id"])


async def completed_build(
    client: httpx.AsyncClient,
    publisher: PublishDriver,
    version_id: str,
    label_column: str | None = "label",
) -> dict[str, Any]:
    response = await client.post(
        f"/api/v1/versions/{version_id}/publish-builds", json={"label_column": label_column}
    )
    assert response.status_code in (200, 202), response.text
    publisher.run_publish_tasks()
    got = await client.get(f"/api/v1/publish-builds/{response.json()['build_id']}")
    assert got.json()["status"] == "completed", got.json()
    data: dict[str, Any] = got.json()
    return data


async def publish(
    client: httpx.AsyncClient,
    publisher: PublishDriver,
    version_id: str,
    build_id: str,
    *,
    repo: str = REPO,
    visibility: str | None = None,
    prose: str = "# Humor\n\nShort texts.",
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    payload: dict[str, Any] = {
        "version_id": version_id,
        "build_id": build_id,
        "repo_id": repo,
        "card_prose": prose,
    }
    if visibility is not None:
        payload["visibility"] = visibility
    return await client.post("/api/v1/publishes", json=payload, headers=headers or {})


async def publish_and_run(
    client: httpx.AsyncClient, publisher: PublishDriver, version_id: str, build_id: str, **kw: Any
) -> dict[str, Any]:
    response = await publish(client, publisher, version_id, build_id, **kw)
    assert response.status_code == 201, response.text
    publisher.run_publish_tasks()
    got = await client.get(f"/api/v1/publishes/{response.json()['publish_id']}")
    data: dict[str, Any] = got.json()
    return data


def owners_report_green(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand-ins for features 004 and 006 reporting a CLEAN version (checked, nothing found).

    The other direction of the seam: with these, a public push is possible; without them the
    same checks are ``not_checked`` and amber.
    """
    from src.services.publishing import feature_seams as seams

    monkeypatch.setattr(
        seams,
        "check_leakage",
        lambda inputs, *, held_out, group_column, session: seams.LeakageFinding(
            seams.CHECKED, 0, {}
        ),
    )
    monkeypatch.setattr(
        seams,
        "evaluate_warnings",
        lambda inputs, *, label_column, session: seams.WarningFinding(seams.CHECKED, [], []),
    )
    monkeypatch.setattr(
        seams,
        "audit_status",
        lambda version_id, *, session: seams.AuditFinding(seams.CHECKED, "complete"),
    )
