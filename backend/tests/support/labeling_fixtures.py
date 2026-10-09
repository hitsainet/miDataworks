"""Fixtures for feature 005: a version with real Parquet, roles pointed at fakes, a job runner.

``labeling`` (the fixture) installs the fake miLLM and TEI behind ``endpoint_caller``'s transport
factory, captures Celery sends instead of using a broker, captures emits, and runs a queued label
job IN PROCESS through the real task body (``label_run_tasks.run_job``) with a fake sleep that
records every wait, so backpressure tests use a simulated clock.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from sqlalchemy import select

from src.core.database import sync_session_factory
from src.core.encryption import encrypt_value
from src.models.app_setting import AppSetting
from src.models.endpoint_role import EndpointRole
from src.models.job import Job
from src.models.label_run import LabelRun, LabelRunJob
from tests.support import db_factories
from tests.support.fake_millm import ORIGIN, TEI_ORIGIN, FakeMillm, FakeTEI, route

KEY = "sk-test-endpoint-key-0123456789"


def row_key(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def make_version(
    data_dir: Path,
    texts: Sequence[str],
    origins: Sequence[str] | None = None,
    *,
    column: str = "text",
) -> str:
    """A completed version whose split file holds ``texts`` (one row each)."""
    version_id = db_factories.uid()
    relative = f"versions/{version_id}/train.parquet"
    with sync_session_factory()() as db:
        path = data_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        table = pa.table(
            {
                "_dw_row_key": [row_key(t) for t in texts],
                "_dw_occurrence": [0] * len(texts),
                "_dw_origin": list(origins) if origins is not None else ["source"] * len(texts),
                "_dw_split": ["train"] * len(texts),
                column: list(texts),
            }
        )
        pq.write_table(table, path)
        db_factories.version(
            db,
            id=version_id,
            splits=[
                {
                    "name": "train",
                    "path": relative,
                    "rows": len(texts),
                    "held_out": False,
                    "bytes": path.stat().st_size,
                    "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "logical_digest": hashlib.sha256(b"labeling-fixture").hexdigest(),
                }
            ],
            total_rows=len(texts),
            column_roles={column: "content"},
        )
        db.commit()
        return version_id


def set_role(
    role: str,
    *,
    base_url: str = ORIGIN + "/v1",
    model: str = "JEV-9B-decision",
    protocol: str | None = "openai_scoring",
    api_key: str | None = KEY,
    use_mode: str = "own",
    inherit: bool = False,
) -> None:
    with sync_session_factory()() as db:
        row = db.get(EndpointRole, role) or EndpointRole(role=role)
        row.protocol = protocol
        row.base_url = base_url
        row.model_id = model
        row.api_key_ciphertext = encrypt_value(api_key) if api_key else None
        row.use_mode = use_mode
        row.inherit_from_judge = inherit
        db.merge(row)
        db.commit()


def set_operator(name: str = "Test Operator") -> None:
    with sync_session_factory()() as db:
        row = db.get(AppSetting, "operator_name")
        if row is None:
            db.add(AppSetting(key="operator_name", value=name))
        else:
            row.value = name
        db.commit()


@dataclass
class Labeling:
    millm: FakeMillm
    tei: FakeTEI
    data_dir: Path
    sent: list[tuple[str, list[Any], dict[str, Any]]] = field(default_factory=list)
    emitted: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    waits: list[float] = field(default_factory=list)
    after_rename: Any = None

    def send_task(self, name: str, args: list[Any] | None = None, **kwargs: Any) -> None:
        self.sent.append((name, list(args or []), kwargs))

    def emit(self, room: str, event: str, data: dict[str, Any]) -> bool:
        self.emitted.append((room, event, data))
        return True

    def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)

    def engine(self) -> Any:
        from src.services.label_run_engine import EngineDeps, LabelRunEngine

        return LabelRunEngine(
            EngineDeps(emit=self.emit, sleep=self.sleep, after_rename=self.after_rename)
        )

    def run_job(self, job_id: str) -> dict[str, Any]:
        from src.workers import label_run_tasks

        return label_run_tasks.run_job(job_id)

    def job_for(self, run_id: str, seq: int = -1) -> str:
        with sync_session_factory()() as db:
            links = list(
                db.execute(
                    select(LabelRunJob)
                    .where(LabelRunJob.label_run_id == run_id)
                    .order_by(LabelRunJob.seq)
                ).scalars()
            )
            return links[seq].job_id

    def run_until_done(self, run_id: str) -> LabelRun:
        self.run_job(self.job_for(run_id))
        return self.run(run_id)

    def run(self, run_id: str) -> LabelRun:
        with sync_session_factory()() as db:
            row = db.get(LabelRun, run_id)
            assert row is not None
            db.expunge(row)
            return row

    def job(self, job_id: str) -> Job:
        with sync_session_factory()() as db:
            row = db.get(Job, job_id)
            assert row is not None
            db.expunge(row)
            return row


@pytest.fixture
def labeling(monkeypatch: pytest.MonkeyPatch, data_dir: Path, clean_db: None) -> Iterator[Labeling]:
    from src.clients import endpoint_caller
    from src.core.celery_app import celery_app
    from src.workers import label_run_tasks

    millm, tei = FakeMillm(), FakeTEI()
    state = Labeling(millm, tei, data_dir)
    previous = endpoint_caller.install_transport(route({ORIGIN: millm, TEI_ORIGIN: tei}))
    monkeypatch.setattr(celery_app, "send_task", state.send_task)
    monkeypatch.setattr(label_run_tasks, "_engine", state.engine)
    monkeypatch.setattr(label_run_tasks, "_next_jobs", lambda: None)
    try:
        yield state
    finally:
        endpoint_caller.install_transport(previous)
