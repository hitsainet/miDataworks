"""Independent review of 020 P0 (rebrand/p0 @ e279edf): regression tests for the survivors and the
code findings. Each test names the control it pins."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from src.core.config import get_settings
from src.core.database import sync_session_factory
from src.services.detector_sets import reproduction_links, reward_marks
from tests.integration.detector_sets.test_borrowed_roles import (
    checks_of,
    d,
    sent_sibling,
    train_only,
)
from tests.integration.detector_sets.test_reproduction_links import labelled_version
from tests.integration.test_previous_origin_lookups import NEW, OLD, _link, _mark, _Side, switch
from tests.support.detector_fixtures import labelled_rows, version
from tests.support.publish_fixtures import publisher  # noqa: F401 - fixture
from tests.support.send_fixtures import API, SendDriver, sender  # noqa: F401 - fixture
from tests.support.version_fixtures import driver  # noqa: F401 - fixture


def test_existing_link_matches_only_its_own_key(
    clean_db: None, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control R06: the duplicate finder must match every column of the link's identity, not only
    the address. A link for the same probe and view but another SPLIT (or version, view, probe) is a
    different link; reporting it as ``link_exists`` would refuse a legitimate link."""
    version_id = labelled_version(data_dir, [("a", "a"), ("b", "b")])
    other_version = labelled_version(data_dir, [("c", "a"), ("d", "b")])
    made = _link(OLD, version_id)
    switch(monkeypatch)
    same: dict[str, Any] = {"version_id": version_id, "split": "test"}
    with sync_session_factory()() as db:
        found = reproduction_links._existing_link(db, _Side(NEW), {"id": "pmd_link"}, same)
        assert found is not None and found.id == made
        for data, view, probe in (
            ({**same, "split": "train"}, "pmd_link", "pm_link"),
            ({**same, "version_id": other_version}, "pmd_link", "pm_link"),
            (same, "pmd_other", "pm_link"),
            (same, "pmd_link", "pm_other"),
        ):
            side = _Side(NEW)
            side.probe_id = probe
            assert reproduction_links._existing_link(db, side, {"id": view}, data) is None, (
                data,
                view,
                probe,
            )


def test_empty_list_does_not_merge_spellings_of_the_current_address(
    clean_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control R12: with no previous origin listed, a lookup is exactly today's text match, even for
    a spelling variant of the CURRENT address (the existing test only varies a non-current one)."""
    _mark(NEW + "/", probe="pm_slash")
    switch(monkeypatch, previous=())
    with sync_session_factory()() as db:
        assert reward_marks.marked_probe_ids(db, NEW) == set()
        assert reward_marks.marked_probe_ids(db, NEW + "/") == {"pm_slash"}


async def test_a_borrowing_set_still_borrows_after_the_miworkbench_cutover(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    sender: SendDriver,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Code finding (set_service.sibling_views): the sibling's views were registered in miWorkbench
    under its OLD address; after P3 the SAME instance answers at NEW with OLD listed as previous.
    D-1 must still find the sibling's completed send, as every other lookup of 020 does."""
    sibling, sibling_send = await sent_sibling(client, sender)
    assert get_settings().mistudio_base_url == OLD
    switch(monkeypatch)
    train_b = version({"train": labelled_rows(200, seed=21, tag="armb")}, dataset_name="arm-b")
    created = await client.post(f"{API}/detector-sets", json=train_only("arm-b", train_b, sibling))
    assert created.status_code == 201, created.text
    d1 = d(await checks_of(client, created.json()["id"]), "D-1")
    assert d1["outcome"] == "green", (d1.get("reason"), d1.get("details"))
    assert d1["details"]["sibling_send_id"] == sibling_send


async def test_a_borrowing_set_sends_after_the_miworkbench_cutover(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    sender: SendDriver,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """send_service.borrowed_views reads the same lookup: after the cutover a borrowing set's send
    reuses the sibling's views (registered under the OLD address) instead of refusing."""
    from tests.support.send_fixtures import NAMESPACE, repos_for, start_send

    sibling, sibling_send = await sent_sibling(client, sender)
    switch(monkeypatch)
    train_b = version({"train": labelled_rows(200, seed=21, tag="armb")}, dataset_name="arm-b")
    created = await client.post(f"{API}/detector-sets", json=train_only("arm-b", train_b, sibling))
    assert created.status_code == 201, created.text
    arm_b = created.json()
    first = await client.post(
        f"{API}/detector-sets/{arm_b['id']}/send", json={"namespace": NAMESPACE}
    )
    assert first.status_code == 409 and first.json()["error"]["code"] == "builds_pending"
    sender.publisher.run_publish_tasks()
    repo_b = repos_for({"train": train_b})[train_b]
    sender.mistudio.values[(repo_b, "train")] = {"humorous": 100, "not_humorous": 100}
    started = await start_send(client, arm_b["id"])
    assert sender.run(started["job_id"])["status"] == "completed"
    got = (await client.get(f"{API}/detector-sends/{started['send_id']}")).json()
    assert {b["from_send_id"] for b in got["borrowed_views"]} == {sibling_send}


async def test_a_borrowing_set_still_borrows_across_a_trailing_slash_with_no_list(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    sender: SendDriver,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review round 2 (survivor S02): ``sibling_views`` keeps ``_same_address`` beside
    ``same_sibling``. With NO previous origin listed, a configured address that differs from the
    stored one only by a trailing slash is the same miWorkbench, as it was before 020; ``same_sibling``
    alone (text equality, then the list) would lose the sibling's views."""
    sibling, sibling_send = await sent_sibling(client, sender)
    stored = get_settings().mistudio_base_url
    assert not stored.endswith("/")
    switch(monkeypatch, current=stored + "/", previous=())
    train_b = version({"train": labelled_rows(200, seed=21, tag="armb")}, dataset_name="arm-b")
    created = await client.post(f"{API}/detector-sets", json=train_only("arm-b", train_b, sibling))
    assert created.status_code == 201, created.text
    d1 = d(await checks_of(client, created.json()["id"]), "D-1")
    assert d1["outcome"] == "green", (d1.get("reason"), d1.get("details"))
    assert d1["details"]["sibling_send_id"] == sibling_send
