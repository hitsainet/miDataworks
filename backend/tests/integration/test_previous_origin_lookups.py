"""Rows keyed by a sibling's address survive its cutover (feature 020 part C; FR-020.12, FR-020.16;
BR-D08; FTASKS 6.5).

Each lookup site (L1-L5, FTID §7.3) is driven through the code that runs it in production: the
reward-mark service, the P-21 trace, the send worker's dataset and view reuse, and the link
duplicate finder. The rows are stored under the OLD miWorkbench address; the current address is
NEW and OLD is listed as previous. An unrelated host stays unmatched, and with the list empty
every lookup is exactly today's text match (FTDD §10.1: three hosts and a spelling variant).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from src.core.config import get_settings
from src.core.database import sync_session_factory
from src.core.ids import new_id
from src.models.detector_results import RewardMark
from src.models.detector_send import MiStudioRegistration
from src.models.reproduction_link import ReproductionLink
from src.services.detector_sets import reproduction_links, reward_marks
from tests.integration.detector_sets.test_reproduction_links import labelled_version
from tests.integration.detector_sets.test_send_manifest import REGISTER, register_steps, resend
from tests.support.fake_miworkbench import BASE
from tests.support.labeling_fixtures import row_key
from tests.support.publish_fixtures import publisher  # noqa: F401 - fixture
from tests.support.send_fixtures import SendDriver, ready_set, sender  # noqa: F401 - fixture
from tests.support.version_fixtures import driver  # noqa: F401 - fixture

OLD = BASE  # "http://mistudio.test": the address the rows were made with
NEW = "http://miworkbench.test"
OTHER = "http://other.test"
DOWNLOAD = "/api/v1/datasets/download"


def switch(
    monkeypatch: pytest.MonkeyPatch, *, current: str = NEW, previous: tuple[str, ...] = (OLD,)
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "mistudio_base_url", current)
    monkeypatch.setattr(settings, "miworkbench_previous_base_urls", previous)


def _mark(base: str, probe: str = "pm_old") -> None:
    with sync_session_factory()() as db:
        db.add(
            RewardMark(
                id=new_id("rwm"),
                mistudio_base_url=base,
                mistudio_probe_id=probe,
                source="operator",
                reason="trained on it",
                marked_by="Test Operator",
                marked_by_origin="operator",
            )
        )
        db.commit()


def _view(base: str, version_id: str, view_id: str = "pmd_train", dataset: str = "ds") -> str:
    with sync_session_factory()() as db:
        row = MiStudioRegistration(
            id=new_id("msr"),
            kind="view",
            mistudio_base_url=base,
            mistudio_dataset_id=f"{dataset}-{base}",
            role="train",
            mapping_sha256="0" * 64,
            columns_sha256="1" * 64,
            probe_dataset_id=view_id,
            version_id=version_id,
            split="test",
        )
        db.add(row)
        db.commit()
        return row.id


def _trace(base: str) -> Any:
    with sync_session_factory()() as db:
        return reward_marks.trace(
            db, base_url=base, probe_id="pm_x", train_view_id="pmd_train", model_id="m", layer=3
        )


# --- L1 marked_probe_ids -------------------------------------------------------------------------


def test_l1_marked_probe_ids_match_a_previous_origin(
    clean_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mark(OLD)
    switch(monkeypatch)
    with sync_session_factory()() as db:
        assert reward_marks.marked_probe_ids(db, NEW) == {"pm_old"}


# --- L2 trace -------------------------------------------------------------------------------------


def test_l2_trace_finds_a_view_registered_under_a_previous_origin(
    clean_db: None, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version_id = labelled_version(data_dir, [("a", "a"), ("b", "b")])
    _view(OLD, version_id)
    switch(monkeypatch)
    trace = _trace(NEW)
    assert trace.missing_link is None, trace
    assert trace.train_row_keys == {row_key("a"), row_key("b")}


def test_l2_trace_with_rows_under_both_origins_picks_the_current_one(
    clean_db: None, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ID-7: two registrations match after ``IN``; the current address's row wins, never
    ``MultipleResultsFound`` (control M53)."""
    old_version = labelled_version(data_dir, [("a", "a"), ("b", "b")])
    new_version = labelled_version(data_dir, [("c", "a"), ("d", "b")])
    _view(NEW, new_version)
    _view(OLD, old_version)
    switch(monkeypatch)
    trace = _trace(NEW)
    assert trace.missing_link is None, trace
    assert trace.train_row_keys == {row_key("c"), row_key("d")}, "the current address's row won"


# --- L3, L4: the send worker reuses its registrations ------------------------------------------


async def _sent_then_switched(
    client: httpx.AsyncClient, drv: SendDriver, monkeypatch: pytest.MonkeyPatch, **switch_kw: Any
) -> str:
    set_id, _, _ = await ready_set(client, drv)
    first = await resend(client, drv, set_id)
    assert drv.send(first).state == "completed"
    switch(monkeypatch, **switch_kw)
    drv.mistudio.requests.clear()
    return set_id


def _registrations() -> int:
    with sync_session_factory()() as db:
        return int(db.scalar(select(func.count()).select_from(MiStudioRegistration)) or 0)


async def test_l3_dataset_registration_reused_across_origins(
    client: httpx.AsyncClient,
    operator_name: str,
    sender: SendDriver,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_id = await _sent_then_switched(client, sender, monkeypatch)
    before = _registrations()
    second = await resend(client, sender, set_id)
    assert sender.send(second).state == "completed", sender.send(second).error
    assert sender.send(second).mistudio_base_url == NEW
    assert sender.mistudio.calls("POST", DOWNLOAD) == [], "no dataset downloaded again"
    assert _registrations() == before


async def test_l4_view_registration_reused_across_origins(
    client: httpx.AsyncClient,
    operator_name: str,
    sender: SendDriver,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_id = await _sent_then_switched(client, sender, monkeypatch)
    second = await resend(client, sender, set_id)
    assert sender.send(second).state == "completed", sender.send(second).error
    assert sender.mistudio.calls("POST", REGISTER) == [], "no view registered again"
    assert {s.state for s in register_steps(sender, second).values()} == {"reused"}


async def test_unrelated_host_stays_unmatched(
    client: httpx.AsyncClient,
    operator_name: str,
    sender: SendDriver,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A switch to an address that is NOT the same instance re-downloads and re-registers."""
    set_id = await _sent_then_switched(client, sender, monkeypatch, current=OTHER, previous=())
    await resend(client, sender, set_id)
    assert sender.mistudio.calls("POST", DOWNLOAD), "the dataset is downloaded afresh, not reused"
    with sync_session_factory()() as db:
        assert reward_marks.marked_probe_ids(db, OTHER) == set()


async def test_empty_previous_list_behaves_as_today(
    client: httpx.AsyncClient,
    operator_name: str,
    sender: SendDriver,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mark(OLD)
    set_id = await _sent_then_switched(client, sender, monkeypatch, previous=())
    await resend(client, sender, set_id)
    assert sender.mistudio.calls("POST", DOWNLOAD), "without a listed origin nothing is reused"
    with sync_session_factory()() as db:
        assert reward_marks.marked_probe_ids(db, NEW) == set()
        assert reward_marks.marked_probe_ids(db, OLD + "/") == set(), "a spelling variant too"


# --- L5: the link duplicate finder ---------------------------------------------------------------


class _Side:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.probe_id = "pm_link"


def _link(base: str, version_id: str) -> str:
    with sync_session_factory()() as db:
        row = ReproductionLink(
            id=new_id("rpl"),
            mistudio_base_url=base,
            mistudio_probe_id="pm_link",
            probe_dataset_id="pmd_link",
            role="id_test",
            version_id=version_id,
            split="test",
            input_column="text",
            label_column="label",
            label_mapping={"a": "positive", "b": "negative"},
            auroc=0.9,
            ci_low=0.89,
            ci_high=0.91,
            n_positive=1,
            n_negative=1,
            check_level="counts_only",
            checks={},
            scoring_form={},
            evidence={},
            created_by="Test Operator",
            created_by_origin="operator",
        )
        db.add(row)
        db.commit()
        return row.id


def test_l5_link_duplicate_found_across_origins(
    clean_db: None, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version_id = labelled_version(data_dir, [("a", "a"), ("b", "b")])
    old_link = _link(OLD, version_id)
    switch(monkeypatch)
    data = {"version_id": version_id, "split": "test"}
    with sync_session_factory()() as db:
        found = reproduction_links._existing_link(db, _Side(NEW), {"id": "pmd_link"}, data)
        assert found is not None and found.id == old_link
        assert reproduction_links._existing_link(db, _Side(OTHER), {"id": "pmd_link"}, data) is None
