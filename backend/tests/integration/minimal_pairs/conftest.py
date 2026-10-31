"""Fixtures for 009's minimal-pair chain: 007's fake miLLM on loopback, 002's REAL build driver over
the PRODUCTION operator registry, and 005's label jobs run in process (FTASKS 15.3)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.support.generation_fixtures import Gen, gen
from tests.support.operator_build_driver import RealDriver

__all__ = ["gen", "chain_env"]


@pytest.fixture
def chain_env(monkeypatch: pytest.MonkeyPatch, gen: Gen) -> Iterator[RealDriver]:
    from src.core.celery_app import celery_app
    from src.operators import executor
    from src.operators import registry as registry_module
    from src.operators.registry import OperatorRegistry
    from src.services import operator_port
    from src.services.label_run_engine import EngineDeps, LabelRunEngine
    from src.workers import label_run_tasks, version_build_tasks

    reg = OperatorRegistry.build(catalogues=(), entry_points=())
    monkeypatch.setattr(operator_port, "_registry", reg)
    monkeypatch.setattr(registry_module, "_process", reg)
    drv = RealDriver(reg)
    monkeypatch.setattr(executor, "send_task", drv.record_step)
    monkeypatch.setattr(celery_app, "send_task", drv.send_task)
    monkeypatch.setattr(version_build_tasks, "_send_task", drv.send_task)
    monkeypatch.setattr(version_build_tasks, "emit", drv.emit)
    monkeypatch.setattr(
        label_run_tasks,
        "_engine",
        lambda: LabelRunEngine(EngineDeps(emit=gen.emit, sleep=gen.sleep)),
    )
    monkeypatch.setattr(label_run_tasks, "_next_jobs", lambda: None)
    yield drv
