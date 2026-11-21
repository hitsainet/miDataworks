"""The DLP Credit Cards dataset rebuilt with the new operators, end to end (e2e DLP run, 2026-10-21).

Real PostgreSQL, the production operator registry, the real executor and ``advance_build``; only the
broker is replaced. Two builds:

1. The LABELLING stage: the hand-written recipe's own generated rows (a stratified 321-row sample
   of its 8,000; the full 8,000 are re-run by hand, ``0xcc/reviews``) labelled by a 4-step recipe
   - checksum_validate, phrase_proximity, rule_label, rule_label - where the hand-written one took
   38 ``dd_expression`` steps. The label column must equal the hand recipe's byte for byte.
2. FROM NOTHING: ``POST /sources/index`` (no upload) -> synth_identifier -> checksum_validate ->
   rule_label -> drop_columns. Every row's label agrees with the generator's intent.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.operators import executor
from src.operators import registry as registry_module
from src.operators.registry import OperatorRegistry
from src.services import operator_port
from tests.integration.test_version_build import request, setup, src
from tests.support import dlp
from tests.support.operator_build_driver import RealDriver
from tests.support.source_fixtures import HfEnv, hf_env
from tests.support.version_fixtures import make_source

__all__ = ["hf_env"]


@pytest.fixture
def production_driver(monkeypatch: pytest.MonkeyPatch, data_dir: Path) -> Iterator[RealDriver]:
    """``RealDriver`` over the registry production builds (natives only: no catalogue worker)."""
    from src.core.celery_app import celery_app
    from src.workers import version_build_tasks

    reg = OperatorRegistry.build(catalogues=(), entry_points=())
    monkeypatch.setattr(operator_port, "_registry", reg)
    monkeypatch.setattr(registry_module, "_process", reg)
    drv = RealDriver(reg)
    monkeypatch.setattr(executor, "send_task", drv.record_step)
    monkeypatch.setattr(celery_app, "send_task", drv.send_task)
    monkeypatch.setattr(version_build_tasks, "_send_task", drv.send_task)
    monkeypatch.setattr(version_build_tasks, "emit", drv.emit)
    yield drv


def _recipe(steps: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    return {
        "format": "dw.recipe/v1",
        "steps": [{"operator": n, "version": "1", "params": p} for n, p in steps],
    }


async def _build(
    client: httpx.AsyncClient,
    driver: RealDriver,
    steps: list[tuple[str, dict[str, Any]]],
    source_id: str,
    roles: dict[str, str],
    name: str,
) -> list[dict[str, Any]]:
    ds, rev = await setup(client, _recipe(steps), name=name)
    response = await request(client, ds, rev, [src(source_id)], seed=20261021, column_roles=roles)
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    assert driver.run(job_id) == "completed", driver.status(job_id)
    from src.core.database import sync_session_factory
    from src.models import Job, Version

    with sync_session_factory()() as db:
        job = db.get(Job, job_id)
        assert job is not None and job.result is not None
        version = db.get(Version, job.result["version_id"])
        assert version is not None
        paths = [s["path"] for s in version.splits]
    from src.core.storage import resolve_under_data_dir

    return pa.concat_tables([pq.read_table(resolve_under_data_dir(p)) for p in paths]).to_pylist()


async def test_four_steps_reproduce_the_hand_written_recipes_labels(
    client: httpx.AsyncClient, production_driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    sample = dlp.hand_sample()
    rows = [{k: r[k] for k in ("idx", "pre", "value", "post", "text")} for r in sample]
    source = make_source(data_dir, {"train": rows}, repo_id="dlp/cc-hand")
    steps = dlp.label_steps("cc", value="value", before="pre", after="post", text="text")
    assert len(steps) == 4
    out = await _build(
        client, production_driver, steps, source, {"text": "content"}, "dlp-cc-labels"
    )
    by_idx = {r["idx"]: r for r in out}
    assert sorted(by_idx) == sorted(r["idx"] for r in sample), "no row dropped or added"
    got = [by_idx[r["idx"]]["label"] for r in sample]
    want = [r["hand_label"] for r in sample]
    digest = lambda xs: hashlib.sha256(json.dumps(xs).encode()).hexdigest()  # noqa: E731
    assert digest(got) == digest(want)
    assert [by_idx[r["idx"]]["tier"] for r in sample] == [r["hand_tier"] for r in sample]


async def test_a_card_dataset_from_nothing(
    client: httpx.AsyncClient,
    hf_env: HfEnv,
    data_dir: Path,
    operator_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = await client.post("/api/v1/sources/index", json={"rows": 1200})
    assert created.status_code == 202, created.text
    [imported] = hf_env.run_imports()
    source_id = imported["source_id"]
    again = await client.post("/api/v1/sources/index", json={"rows": 1200})
    assert again.status_code == 200 and again.json()["id"] == source_id, "content-addressed"

    from src.core.celery_app import celery_app
    from src.workers import version_build_tasks

    reg = OperatorRegistry.build(catalogues=(), entry_points=())
    monkeypatch.setattr(operator_port, "_registry", reg)
    monkeypatch.setattr(registry_module, "_process", reg)
    driver = RealDriver(reg)
    monkeypatch.setattr(executor, "send_task", driver.record_step)
    monkeypatch.setattr(celery_app, "send_task", driver.send_task)
    monkeypatch.setattr(version_build_tasks, "_send_task", driver.send_task)
    monkeypatch.setattr(version_build_tasks, "emit", driver.emit)

    steps = [
        ("synth_identifier", {"scheme": "card_pan", "seed": 11, "valid_share": 0.5}),
        ("checksum_validate", {"column": "value", "scheme": "card_pan"}),
        (
            "rule_label",
            {"rules": ["present if cv_tier == medium"], "default": "absent"},
        ),
        ("drop_columns", {"patterns": ["cv_normalised", "cv_start", "cv_end"]}),
    ]
    out = await _build(client, driver, steps, source_id, {"idx": "content"}, "dlp-cc-synth")
    assert len(out) == 1200 and sorted(r["idx"] for r in out) == list(range(1200))
    assert "cv_start" not in out[0] and "cv_tier" in out[0]
    for r in out:
        assert r["valid"] == r["cv_valid"], r
        if r["perturbation"] == "none":
            assert r["label"] == "present", r
        if r["perturbation"] in (
            "bad_check_digit",
            "wrong_length",
            "bad_prefix",
            "masked",
            "adjacent_letters",
        ):
            assert r["label"] == "absent", r
    kinds = Counter(r["perturbation"] for r in out)
    assert set(kinds) == {
        "none",
        "bad_check_digit",
        "wrong_length",
        "mixed_delimiters",
        "adjacent_letters",
        "bad_prefix",
        "masked",
    }
