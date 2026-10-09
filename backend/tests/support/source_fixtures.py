"""Fixtures for feature 001's integration tests (001 FTID section 8).

``hf_env`` puts the recorded Hugging Face answers behind the worker's client, a ``FakeLoader``
behind ``load_dataset``, captures every Socket.IO emit and every Celery send, and runs a sent
``preview_hf`` or ``import_source`` in-process with the real task code — so a route test
exercises route → job → worker → database → files with only the network replaced.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from src.core.database import sync_session_factory
from src.models.job import Job
from tests.support.hf_mock import FakeLoader, HfMock, client_factory


class _Result:
    """Stands in for a Celery ``AsyncResult``: ``get`` runs the task in-process."""

    def __init__(self, run: Any) -> None:
        self._run = run

    def get(self, timeout: float | None = None) -> Any:
        return self._run()


@dataclass
class HfEnv:
    mock: HfMock
    loader: FakeLoader
    sent: list[tuple[str, list[Any]]] = field(default_factory=list)
    emitted: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    preview_timeout: bool = False

    def send_task(self, name: str, args: list[Any] | None = None, **_: Any) -> Any:
        from src.workers import source_tasks

        args = list(args or [])
        self.sent.append((name, args))
        if name == "midataworks.sources.preview_hf":
            if self.preview_timeout:
                from celery.exceptions import TimeoutError as CeleryTimeout

                def late() -> Any:
                    raise CeleryTimeout("no answer")

                return _Result(late)
            return _Result(lambda: source_tasks.preview_hf(*args))
        return _Result(lambda: None)

    def emit(self, room: str, event: str, data: dict[str, Any]) -> bool:
        self.emitted.append((room, event, data))
        return True

    def run_imports(self) -> list[dict[str, Any]]:
        """Run every ``import_source`` sent so far, in order (as the curation worker would)."""
        from src.workers import source_tasks

        results = []
        while True:
            pending = [a for n, a in self.sent if n == "midataworks.sources.import_source"]
            self.sent = [s for s in self.sent if s[0] != "midataworks.sources.import_source"]
            if not pending:
                return results
            for args in pending:
                results.append(source_tasks.import_source(*args))

    def job(self, job_id: str) -> Job:
        with sync_session_factory()() as db:
            row = db.execute(select(Job).where(Job.id == job_id)).scalar_one()
            db.expunge(row)
            return row


@pytest.fixture
def hf_env(monkeypatch: pytest.MonkeyPatch, data_dir: Path) -> Iterator[HfEnv]:
    from src.core.celery_app import celery_app
    from src.workers import source_tasks

    env = HfEnv(HfMock(), FakeLoader())
    monkeypatch.setattr(celery_app, "send_task", env.send_task)
    monkeypatch.setattr(source_tasks, "_client", client_factory(env.mock))
    monkeypatch.setattr(source_tasks, "LOADER", env.loader)
    monkeypatch.setattr(source_tasks, "emit", env.emit, raising=False)
    yield env


def source_row(source_id: str) -> Any:
    from src.models.source import Source

    with sync_session_factory()() as db:
        row = db.get(Source, source_id)
        assert row is not None
        db.expunge(row)
        return row


def sources_where(**filters: Any) -> list[Any]:
    from src.models.source import Source

    with sync_session_factory()() as db:
        query = select(Source)
        for column, value in filters.items():
            query = query.where(getattr(Source, column) == value)
        rows = list(db.execute(query.order_by(Source.created_at)).scalars())
        for row in rows:
            db.expunge(row)
        return rows


def file_rows(source_id: str) -> list[Any]:
    from src.models.source import SourceFile

    with sync_session_factory()() as db:
        rows = list(
            db.execute(
                select(SourceFile)
                .where(SourceFile.source_id == source_id)
                .order_by(SourceFile.split)
            ).scalars()
        )
        for row in rows:
            db.expunge(row)
        return rows
