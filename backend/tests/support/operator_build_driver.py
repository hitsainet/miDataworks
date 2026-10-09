"""Drive feature 002's build through feature 003's REAL executor, without a broker.

Like ``version_fixtures.BuildDriver`` but the registry is 003's ``OperatorRegistry`` and a
dispatched step runs the real Celery task body (``operator_tasks.step_curation.run``); then its
recorded ``link`` is followed exactly as Celery would, into ``advance_build``. The link's task name
and args come from what ``dispatch_step`` actually sent, so a dropped or wrong link fails here.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from src.core.database import sync_session_factory
from src.models.job import Job
from src.operators import executor
from src.operators import registry as registry_module
from src.operators.registry import OperatorRegistry
from src.services import operator_port
from tests.support import operator_fixtures as fx

ADVANCE = "midataworks.versions.advance_build"


@dataclass
class RealDriver:
    registry: OperatorRegistry
    steps: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    sent: list[tuple[str, list[Any]]] = field(default_factory=list)
    emitted: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    step_results: list[dict[str, Any]] = field(default_factory=list)

    def record_step(self, name: str, **options: Any) -> None:
        self.steps.append((name, options))

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

    def run_step(self) -> list[Any] | None:
        """Run the oldest dispatched step in-process; return the link's args if it names 002."""
        from src.workers import operator_tasks

        name, options = self.steps.pop(0)
        task = {
            "midataworks.operators.step.curation": operator_tasks.step_curation,
            "midataworks.operators.step.labeling": operator_tasks.step_labeling,
        }[name]
        self.step_results.append(task.run(*options["args"]))
        link = options.get("link")
        if link is None or link["task"] != ADVANCE:
            return None
        return list(link["args"])

    def run(self, job_id: str, *, max_passes: int = 200) -> str:
        from src.workers import version_build_tasks

        queue = [job_id]
        for _ in range(max_passes):
            if not queue:
                if not self.steps:
                    break
                args = self.run_step()
                if args:
                    queue.append(args[0])
                continue
            current = queue.pop(0)
            version_build_tasks.run_pass(current)
            for name, args in self.sent:
                if name == ADVANCE and args:
                    queue.append(args[0])
            self.sent = [s for s in self.sent if s[0] != ADVANCE]
        return self.status(job_id)


@pytest.fixture
def real_driver(monkeypatch: pytest.MonkeyPatch, data_dir: Path) -> Iterator[RealDriver]:
    from src.core.celery_app import celery_app
    from src.workers import version_build_tasks

    reg = fx.registry()
    monkeypatch.setattr(operator_port, "_registry", reg)
    monkeypatch.setattr(registry_module, "_process", reg)
    drv = RealDriver(reg)
    monkeypatch.setattr(executor, "send_task", drv.record_step)
    monkeypatch.setattr(celery_app, "send_task", drv.send_task)
    monkeypatch.setattr(version_build_tasks, "_send_task", drv.send_task)
    monkeypatch.setattr(version_build_tasks, "emit", drv.emit)
    yield drv
