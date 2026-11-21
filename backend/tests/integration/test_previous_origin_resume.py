"""Paused runs started on an old miServer address resume after the cutover (feature 020;
FR-020.12; BR-D08; R1-R4; FTASKS 6.11).

Each run is STARTED with the old address, then the address moves (the endpoint role is re-pointed,
or ``MISERVER_BASE_URL`` changes) while the old origin is listed in
``MISERVER_PREVIOUS_BASE_URLS``, and the run's job is executed: it must not refuse
``ENDPOINT_CHANGED``. Both origins reach the same fake instance. An unrelated server, or a changed
model, still refuses exactly as before.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from src.clients import endpoint_caller
from src.core.config import get_settings
from src.core.database import get_sync_engine
from tests.integration.detector_sets import test_feature_tag_runs as features
from tests.integration.detector_sets.test_probe_verdict_runs import (  # noqa: F401 - fixtures
    LLAMA,
    millm,
    refreshed,
    rows,
)
from tests.integration.detector_sets.test_probe_verdict_runs import (
    body as probe_body,
)
from tests.integration.generation.test_standard_run import start as start_generation
from tests.integration.labeling.helpers import setup_classifier, start_body
from tests.support.fake_miserver import ORIGIN, TEI_ORIGIN, route
from tests.support.generation_fixtures import (
    GEN_MODEL,
    Gen,
    gen,  # noqa: F401 - fixture
    respond_template,
    run_body,
    texts,
    version_table,
)
from tests.support.generation_fixtures import make_version as gen_version
from tests.support.labeling_fixtures import Labeling, labeling, make_version  # noqa: F401
from tests.support.publish_fixtures import publisher  # noqa: F401 - fixture
from tests.support.send_fixtures import API, SendDriver, sender  # noqa: F401 - fixture
from tests.support.version_fixtures import driver  # noqa: F401 - fixture

NEW = "http://miserver.test"
OTHER = "http://other.test"


def serve_both(labeling: Labeling, *origins: str) -> None:  # noqa: F811
    """Every origin reaches the SAME fake miServer (one instance, two addresses)."""
    table = {ORIGIN: labeling.millm, TEI_ORIGIN: labeling.tei}
    table.update(dict.fromkeys(origins, labeling.millm))
    endpoint_caller.install_transport(route(table))


def move(
    monkeypatch: pytest.MonkeyPatch, current: str, previous: tuple[str, ...] = (ORIGIN,)
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "millm_base_url", current)
    monkeypatch.setattr(settings, "miserver_previous_base_urls", previous)


def repoint(role: str, base_url: str, model: str | None = None) -> None:
    with get_sync_engine().begin() as conn:
        conn.execute(
            text("UPDATE dw_endpoint_roles SET base_url = :u WHERE role = :r"),
            {"u": base_url, "r": role},
        )
        if model is not None:
            conn.execute(
                text("UPDATE dw_endpoint_roles SET model_id = :m WHERE role = :r"),
                {"m": model, "r": role},
            )


def code_of(run: Any) -> str | None:
    return (run.error or {}).get("code") if run.state == "failed" else None


# --- R1: a score run (the classifier role's address) -------------------------------------------


async def _score_run_moved(
    client: httpx.AsyncClient,
    labeling: Labeling,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    to: str,
    previous: tuple[str, ...],
    model: str | None = None,
) -> Any:
    version_id, template_id = await setup_classifier(client, labeling, n=3)
    started = await client.post("/api/v1/label-runs", json=start_body(version_id, template_id))
    assert started.status_code == 201, started.text
    repoint("classifier", to + "/v1", model)
    move(monkeypatch, to, previous)
    serve_both(labeling, to)
    return labeling.run_until_done(started.json()["id"])


async def test_r1_score_run_resumes_across_origins(
    client: httpx.AsyncClient,
    labeling: Labeling,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    final = await _score_run_moved(client, labeling, monkeypatch, NEW, (ORIGIN,))
    assert final.state == "completed", final.error


async def test_unrelated_host_still_refuses_endpoint_changed(
    client: httpx.AsyncClient,
    labeling: Labeling,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    final = await _score_run_moved(client, labeling, monkeypatch, OTHER, ())
    assert code_of(final) == "ENDPOINT_CHANGED"


async def test_model_id_change_still_refuses(
    client: httpx.AsyncClient,
    labeling: Labeling,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    final = await _score_run_moved(
        client, labeling, monkeypatch, NEW, (ORIGIN,), model="another-model"
    )
    assert code_of(final) == "ENDPOINT_CHANGED"


# --- R2: a probe-verdict run (MISERVER_BASE_URL itself) ----------------------------------------


async def test_r2_probe_run_resumes_across_origins(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    millm: Labeling,  # noqa: F811
    sender: SendDriver,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await refreshed(client, sender)
    version_id = make_version(data_dir, rows(4))
    started = await client.post(f"{API}/label-runs", json=probe_body(version_id))
    assert started.status_code == 201, started.text
    move(monkeypatch, NEW)
    serve_both(millm, NEW)
    final = millm.run_until_done(started.json()["id"])
    assert final.state == "completed", final.error
    assert millm.millm.calls("/api/probes/score", "POST"), "the run scored after the move"


# --- R3: a feature-tag run --------------------------------------------------------------------


async def test_r3_feature_run_resumes_across_origins(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    labeling: Labeling,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    labeling.millm.resident = dict(LLAMA)
    labeling.millm.attachments = [{"sae_id": "sae_l16", "layer": 16}]
    monkeypatch.setattr(get_settings(), "millm_base_url", ORIGIN)
    run = labeling
    version_id = make_version(data_dir, features.TEXTS)
    started = await client.post(f"{API}/label-runs", json=features.body(version_id))
    assert started.status_code == 201, started.text
    move(monkeypatch, NEW)
    serve_both(run, NEW)
    final = run.run_until_done(started.json()["id"])
    assert final.state == "completed", final.error


# --- R4: a generation run (the generation role's address) --------------------------------------


async def test_r4_generation_run_resumes_across_origins(
    client: httpx.AsyncClient,
    gen: Gen,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The generation server answers on 127.0.0.1 and on localhost: one instance, two origins."""
    from tests.support.generation_fixtures import set_role

    current = gen.base_url  # http://127.0.0.1:<port>/v1
    old = current.replace("127.0.0.1", "localhost")
    set_role("generation", old, GEN_MODEL)
    version = gen_version(version_table(texts(2)))
    template = await respond_template(client)
    out = await start_generation(client, run_body(version, template))
    repoint("generation", current)
    move(monkeypatch, current.removesuffix("/v1"), (old.removesuffix("/v1"),))
    run = gen.run_until_done(out["id"])
    assert run.state == "completed", run.error
