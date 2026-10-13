"""Fixtures for feature 002's build tests (FTID 002 section 8; FPRD 002 section 12.7).

Two real sources written as Parquet under the test's data volume, with their SHA-256 recorded the
way feature 001 records it, holding the cases a fixture must not paper over: duplicate texts,
rows that differ only by spacing ("Trump 's" / "Trump's", as Humicroedit did), and labels and
scores for the band and balance operators.

``BuildDriver`` plays Celery: it runs ``advance_build``'s real task body (``run_pass``), runs the
stub executor for whatever was dispatched, and repeats until the job is terminal — the shape of a
``link`` chain without a broker.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.core.database import sync_session_factory
from src.models import Job, Source, SourceFile
from tests.support.stub_operators import StubRegistry, install_stubs

HUMOR_TRAIN: list[dict[str, Any]] = [
    {"text": "Why did the chicken cross the road?", "label": 1, "score": 0.91, "note": "a"},
    {"text": "Why did the chicken cross the road?", "label": 1, "score": 0.91, "note": "b"},
    {"text": "Trump 's tax plan, explained", "label": 0, "score": 0.12, "note": None},
    {"text": "Trump's tax plan, explained", "label": 0, "score": 0.15, "note": None},
    {
        "text": "I told my wife she was drawing her eyebrows too high.",
        "label": 1,
        "score": 0.83,
        "note": None,
    },
    {"text": "Quarterly earnings beat expectations", "label": 0, "score": 0.05, "note": None},
    {"text": "short", "label": 0, "score": 0.5, "note": None},
    {"text": "A pun is its own reword.", "label": 1, "score": 0.55, "note": None},
    {"text": "Rates held steady this month", "label": 0, "score": 0.48, "note": None},
    {
        "text": "Time flies like an arrow; fruit flies like a banana.",
        "label": 1,
        "score": 0.77,
        "note": None,
    },
]
HUMOR_TEST: list[dict[str, Any]] = [
    {"text": "I'm reading a book about anti-gravity.", "label": 1, "score": 0.88, "note": None},
    {"text": "The committee met on Tuesday", "label": 0, "score": 0.08, "note": None},
    {"text": "tiny", "label": 1, "score": 0.6, "note": None},
]
OTHER_SOURCE: list[dict[str, Any]] = [
    {"text": "Why did the chicken cross the road?", "label": 1, "score": 0.9, "note": "c"},
    {"text": "Parallel lines have so much in common.", "label": 1, "score": 0.7, "note": None},
]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_source(
    data_dir: Path,
    splits: dict[str, list[dict[str, Any]]],
    *,
    repo_id: str = "org/humor",
    commit: str | None = None,
    text_columns: tuple[str, ...] = ("text",),
    licence: Any = "cc-by-2.0",
) -> str:
    """A ``ready`` source with one Parquet file per split, recorded as feature 001 records it."""
    source_id = str(uuid.uuid4())
    directory = data_dir / "sources" / source_id
    directory.mkdir(parents=True)
    with sync_session_factory()() as db:
        db.add(
            Source(
                id=source_id,
                kind="hf",
                state="ready",
                display_name=repo_id,
                repo_id=repo_id,
                resolved_commit=commit
                or hashlib.sha1(source_id.encode()).hexdigest(),  # noqa: S324
                licence_raw=licence,
                licence_display=licence if isinstance(licence, str) else "not stated",
                licence_origin="card_data" if licence is not None else "none",
                detection={"text_columns": list(text_columns), "label_columns": ["label"]},
                library_versions={
                    "datasets": "t",
                    "huggingface_hub": "t",
                    "pyarrow": pa.__version__,
                },
                created_by="Test Operator",
                created_by_origin="operator",
            )
        )
        db.flush()
        for split, rows in splits.items():
            path = directory / f"{split}.parquet"
            table = pa.Table.from_pylist(rows)
            pq.write_table(table, path)
            db.add(
                SourceFile(
                    id=str(uuid.uuid4()),
                    source_id=source_id,
                    split=split,
                    path=f"sources/{source_id}/{split}.parquet",
                    rows=table.num_rows,
                    bytes=path.stat().st_size,
                    sha256=sha256_file(path),
                    columns=[{"name": f.name, "type": str(f.type)} for f in table.schema],
                )
            )
        db.commit()
    return source_id


@dataclass
class BuildDriver:
    stubs: StubRegistry
    sent: list[tuple[str, list[Any]]] = field(default_factory=list)
    emitted: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)

    def send_task(self, name: str, args: list[Any] | None = None, **_: Any) -> None:
        self.sent.append((name, list(args or [])))

    def emit(self, room: str, event: str, data: dict[str, Any]) -> bool:
        self.emitted.append((room, event, data))
        return True

    def status(self, job_id: str) -> str:
        with sync_session_factory()() as db:
            job = db.get(Job, job_id)
            assert job is not None
            return job.status

    def run(self, job_id: str, *, max_passes: int = 200) -> str:
        """Drive one build (and any build it re-kicks) to a terminal state."""
        from src.workers import version_build_tasks

        queue = [job_id]
        for _ in range(max_passes):
            if not queue:
                if self.stubs.pending:
                    self.stubs.run_pending()
                    queue.append(job_id)
                    continue
                break
            current = queue.pop(0)
            version_build_tasks.run_pass(current)
            for name, args in self.sent:
                if name == "midataworks.versions.advance_build" and args:
                    queue.append(args[0])
            self.sent = [s for s in self.sent if s[0] != "midataworks.versions.advance_build"]
            if self.stubs.pending:
                self.stubs.run_pending()
                queue.append(current)
            if self.status(job_id) in {"completed", "failed", "cancelled"} and not queue:
                break
        return self.status(job_id)


@pytest.fixture
def driver(monkeypatch: pytest.MonkeyPatch, data_dir: Path) -> Iterator[BuildDriver]:
    from src.core.celery_app import celery_app
    from src.workers import version_build_tasks

    stubs = install_stubs(monkeypatch)
    drv = BuildDriver(stubs)
    monkeypatch.setattr(celery_app, "send_task", drv.send_task)
    monkeypatch.setattr(version_build_tasks, "_send_task", drv.send_task)
    monkeypatch.setattr(version_build_tasks, "emit", drv.emit)
    yield drv
