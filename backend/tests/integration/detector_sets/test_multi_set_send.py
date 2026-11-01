"""Several sets under one approval: a behaviour's main set and a sub-question set (017 FTASKS
10.10; FR-017.30, FR-017.50, FR-017.52; TD-017.29).

The real send worker and 008's real publish; only miStudio's HTTP is the fake. The sub-question set
binds the SAME versions as the main set with another label column (``format``) and the same
behaviour key. An agent's multi-set send files ONE ``hub_push`` approval listing both sets and
every version; approved, it starts one send per set under one ``send_group_id``; miStudio registers
one view per set and role with that set's label column and the shared behaviour (payload and call
count); each shared version is published ONCE. More sets than ``DETECTOR_MAX_SETS_PER_SEND`` is
refused naming the setting. Controls: the specialist binding removed; the route's tool removed.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import httpx
import pyarrow.parquet as pq
import pytest
from sqlalchemy import func, select

from src.core.config import get_settings
from src.core.database import sync_session_factory
from src.core.storage import resolve_under_data_dir
from src.models import Approval, Publish
from src.models.detector_send import DetectorSend
from src.models.version import Version
from tests.support.detector_fixtures import set_body
from tests.support.send_fixtures import API, NAMESPACE, SendDriver, ready_set

AGENT = {"X-Dataworks-Agent": "agent:dataworks-mcp"}
REGISTER = "/api/v1/probe-monitors/datasets"
KEY = "fixture-alpha"
FORMAT_MAPPING = {"joke": "positive", "headline": "negative"}


def format_counts(version_id: str, split: str) -> dict[str, int]:
    with sync_session_factory()() as db:
        version = db.get(Version, version_id)
        assert version is not None
        (entry,) = [s for s in version.splits if s["name"] == split]
    values = pq.read_table(resolve_under_data_dir(entry["path"]), columns=["format"])
    return dict(Counter(str(v) for v in values.column(0).to_pylist()))


async def two_sets(
    client: httpx.AsyncClient, sender: SendDriver
) -> tuple[str, str, dict[str, str], dict[str, str]]:
    """The main set (label) and its sub-question set (format) over the same versions."""
    sender.mistudio.serves_behaviour = True
    main, versions, repos = await ready_set(client, sender, body_over={"behaviour_key": KEY})
    body = set_body(versions, name="humor-set-format", behaviour_key=KEY)
    for role in body["roles"]:
        role["label_column"] = "format"
        role["label_mapping"] = (
            {"headline": "negative"} if role["role"] == "calibration_negatives" else FORMAT_MAPPING
        )
    created = await client.post(f"{API}/detector-sets", json=body)
    assert created.status_code == 201, created.text
    sub = created.json()["id"]
    # the sub-question set's publish files are written for its label column first (008 builds)
    first = await client.post(f"{API}/detector-sets/{sub}/send", json={"namespace": NAMESPACE})
    assert first.status_code == 409 and first.json()["error"]["code"] == "builds_pending"
    sender.publisher.run_publish_tasks()
    for name, split in (("train", "train"), ("train", "test"), ("ood", "test"), ("cal", "train")):
        sender.mistudio.values_by_column[(repos[versions[name]], split, "format")] = format_counts(
            versions[name], split
        )
    return main, sub, versions, repos


async def test_main_and_sub_question_sets_go_under_one_approval(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    main, sub, versions, repos = await two_sets(client, sender)
    pending = await client.post(
        f"{API}/detector-sets/send",
        json={"set_ids": [main, sub], "namespace": NAMESPACE},
        headers=AGENT,
    )
    assert pending.status_code == 202, pending.text
    assert pending.json()["action"] == "hub_push"
    with sync_session_factory()() as db:
        assert db.execute(select(func.count()).select_from(Approval)).scalar_one() == 1
        approval = db.get(Approval, pending.json()["approval_id"])
        assert approval is not None
        payload = approval.payload
    assert [x["set_id"] for x in payload["sends"]] == [main, sub]
    assert set(payload["repositories"]) == set(versions.values())
    assert "2 detector set(s)" in approval.summary and "humor-set-format" in approval.summary
    approved = await client.post(f"/api/v1/approvals/{pending.json()['approval_id']}/approve")
    assert approved.status_code == 200, approved.text
    with sync_session_factory()() as db:
        sends = list(db.execute(select(DetectorSend).order_by(DetectorSend.created_at)).scalars())
    assert {s.set_id for s in sends} == {main, sub}
    assert {s.send_group_id for s in sends} == {payload["send_group_id"]}
    assert {s.id for s in sends} == {x["send_id"] for x in payload["sends"]}
    by_set = {s.set_id: s for s in sends}
    for set_id in (main, sub):  # the group's sends run in order, one at a time
        result = sender.run(by_set[set_id].job_id)
        assert result["status"] == "completed", sender.send(by_set[set_id].id).error
    regs = sender.mistudio.calls("POST", REGISTER)
    assert len(regs) == 8  # one view per set and role
    columns = Counter(c.body["label_column"] for c in regs)
    assert columns == {"label": 4, "format": 4}
    assert {c.body["behaviour"] for c in regs} == {KEY}
    with sync_session_factory()() as db:
        published = Counter(
            p.repo_id for p in db.execute(select(Publish)).scalars() if p.status == "published"
        )
    assert published == dict.fromkeys(repos.values(), 1), published


async def test_more_sets_than_the_cap_is_refused_naming_it(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    sender: SendDriver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    main, sub, _, _ = await two_sets(client, sender)
    monkeypatch.setattr(get_settings(), "detector_max_sets_per_send", 1)
    for headers in ({}, AGENT):
        refused = await client.post(
            f"{API}/detector-sets/send",
            json={"set_ids": [main, sub], "namespace": NAMESPACE},
            headers=headers,
        )
        assert refused.status_code == 422, refused.text
        error: dict[str, Any] = refused.json()["error"]
        assert error["code"] == "cap_exceeded"
        assert error["details"]["setting"] == "DETECTOR_MAX_SETS_PER_SEND"
    with sync_session_factory()() as db:
        assert db.execute(select(func.count()).select_from(Approval)).scalar_one() == 0


async def test_a_set_changed_after_the_group_approval_does_not_send(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    """Review R4: the ONE approval binds every set's digest. The sub-question set's meaning
    changed after the agent asked: the approved replay is refused ``approval_mismatch``, no send
    row, no publish, no registration."""
    main, sub, _, _ = await two_sets(client, sender)
    pending = await client.post(
        f"{API}/detector-sets/send",
        json={"set_ids": [main, sub], "namespace": NAMESPACE},
        headers=AGENT,
    )
    assert pending.status_code == 202, pending.text
    changed = await client.patch(
        f"{API}/detector-sets/{sub}", json={"positive_meaning": "a joke about format"}
    )
    assert changed.status_code == 200, changed.text
    await client.post(f"/api/v1/approvals/{pending.json()['approval_id']}/approve")
    with sync_session_factory()() as db:
        approval = db.get(Approval, pending.json()["approval_id"])
        assert approval is not None and approval.status == "failed", approval
        assert (approval.error or {}).get("code") == "approval_mismatch", approval.error
        assert db.execute(select(func.count()).select_from(DetectorSend)).scalar_one() == 0
        assert db.execute(select(func.count()).select_from(Publish)).scalar_one() == 0
    assert sender.mistudio.calls("POST", REGISTER) == []
