"""A mark or link made under a previous miWorkbench origin is the same record (feature 020;
FR-020.16; TPD-7 W1, W2; FTASKS 6.9).

The unique indexes include the address, so after a cutover they would admit a second row under the
new origin. The services check first, across origins, and answer the same refusal as today."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from src.core.database import sync_session_factory
from src.models.detector_results import RewardMark
from tests.integration.detector_sets.test_reproduction_links import (
    gate_texts,
    labelled_version,
    link_body,
    links,
    studio,  # noqa: F401 - fixture
)
from tests.integration.test_previous_origin_lookups import OLD, switch
from tests.support.fake_miworkbench import FakeMiWorkbench
from tests.support.send_fixtures import API


def _marks() -> int:
    with sync_session_factory()() as db:
        return int(db.scalar(select(func.count()).select_from(RewardMark)) or 0)


async def test_old_origin_mark_reported_already_marked(
    client: httpx.AsyncClient,
    operator_name: str,
    studio: FakeMiWorkbench,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = {"mistudio_probe_id": "pm_once", "reason": "trained on it"}
    first = await client.post(f"{API}/reward-marks", json=body)
    assert first.status_code in (200, 201), first.text
    assert first.json()["mistudio_base_url"] == OLD
    switch(monkeypatch)
    again = await client.post(f"{API}/reward-marks", json=body)
    assert again.status_code == 409, again.text
    assert again.json()["error"]["code"] == "already_marked"
    assert _marks() == 1


async def test_old_origin_link_reported_link_exists(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    studio: FakeMiWorkbench,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version_id = labelled_version(data_dir, gate_texts())
    made = await client.post(f"{API}/reproduction-links", json=link_body(version_id))
    assert made.status_code == 201, made.text
    switch(monkeypatch)
    again = await client.post(f"{API}/reproduction-links", json=link_body(version_id))
    assert again.status_code == 409, again.text
    error = again.json()["error"]
    assert error["code"] == "link_exists" and error["details"]["link_id"] == made.json()["id"]
    assert len(links()) == 1
