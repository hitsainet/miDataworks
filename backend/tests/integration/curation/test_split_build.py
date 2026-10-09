"""The split operator inside a real build: 003's executor and effect checker, 002's finalize
reading ``split_roles``, and the post-commit enqueue of the audit (FTASKS 7.3, 11.5)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from src.core.database import sync_session_factory
from src.models.version import Version
from src.operators import executor
from src.operators import registry as registry_module
from src.services import operator_port
from tests.integration.test_version_build import request, setup, src
from tests.support.curation_fixtures import registry
from tests.support.operator_build_driver import RealDriver
from tests.support.version_fixtures import make_source

POST_VERSION = "midataworks.curation.post_version"


@pytest.fixture
def driver(monkeypatch: pytest.MonkeyPatch, data_dir: Path) -> Iterator[RealDriver]:
    from src.core.celery_app import celery_app
    from src.workers import version_build_tasks

    reg = registry()
    monkeypatch.setattr(operator_port, "_registry", reg)
    monkeypatch.setattr(registry_module, "_process", reg)
    drv = RealDriver(reg)
    monkeypatch.setattr(executor, "send_task", drv.record_step)
    monkeypatch.setattr(celery_app, "send_task", drv.send_task)
    monkeypatch.setattr(version_build_tasks, "_send_task", drv.send_task)
    monkeypatch.setattr(version_build_tasks, "emit", drv.emit)
    yield drv


def _rows(n: int) -> list[dict[str, Any]]:
    return [
        {"text": f"row number {i} " + "x" * (i % 5), "label": "a" if i % 3 else "b"}
        for i in range(n)
    ]


def _body(*steps: tuple[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "format": "dw.recipe/v1",
        "steps": [{"operator": n, "version": "1.0.0", "params": p} for n, p in steps],
    }


async def _build(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, body: dict
) -> Version:
    source = make_source(data_dir, {"train": _rows(100)})
    ds, rev = await setup(client, body)
    response = await request(client, ds, rev, [src(source)], seed=1)
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    assert driver.run(job_id) == "completed", driver.step_results
    with sync_session_factory()() as db:
        version = db.query(Version).filter(Version.dataset_id == ds).one()
        db.expunge(version)
    return version


async def test_split_roles_reach_finalize(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    body = _body(
        (
            "split",
            {
                "split_names": ["train", "test"],
                "split_fractions": [0.8, 0.2],
                "held_out": ["test"],
                "stratify_by": ["label"],
            },
        )
    )
    version = await _build(client, driver, data_dir, body)
    splits = {s["name"]: s for s in version.splits}
    assert splits["test"]["held_out"] is True and splits["train"]["held_out"] is False
    assert splits["test"]["rows"] == 20 and splits["train"]["rows"] == 80


async def test_build_enqueues_audit(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    version = await _build(
        client,
        driver,
        data_dir,
        _body(("metadata_value_filter", {"column": "label", "values": ["zzz"]})),
    )
    sends = [args for name, args in driver.sent if name == POST_VERSION]
    assert sends == [[version.id]]  # payload: the version id; call count: exactly one


async def test_post_version_checks_leakage_after_a_split(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    """FR-004.50: a version whose last step is a split gets its leakage report, with the split
    step's group column."""
    from src.models import VersionReport
    from src.workers import curation_tasks

    body = _body(
        (
            "split",
            {
                "split_names": ["train", "test"],
                "split_fractions": [0.8, 0.2],
                "held_out": ["test"],
                "group_column": "text",
            },
        )
    )
    version = await _build(client, driver, data_dir, body)
    out = curation_tasks.post_version(version.id)
    assert out["leakage"]["report_id"] is not None, (out, version.splits)
    with sync_session_factory()() as db:
        report = db.get(VersionReport, out["leakage"]["report_id"])
        assert report.kind == "leakage" and report.params["group_column"] == "text"
