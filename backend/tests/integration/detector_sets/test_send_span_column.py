"""A sent view carries its evidence offsets (ENH-006 G39; operator decision 2026-11-09).

miWorkbench recommends a probe's pooling rule only from a view's ``span_column``; without one it recommends
nothing. The REAL send worker and 008's REAL publish against the fake miWorkbench. The listing comes from the
manifest the publish really wrote (``extensions.evidence_spans``); which rule-set outputs ARE span outputs
is proven over the real chain in ``tests/integration/behaviour/test_evidence_spans_travel.py``, so here the
rule sets' outputs are given (``span_outputs``) and everything after them is real.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from src.core.database import get_sync_engine, sync_session_factory
from src.models.publish import Publish
from src.services.behaviour import evidence_spans
from src.services.detector_sets import capabilities
from tests.integration.detector_sets.test_send_group_columns import (
    BEHAVIOUR_ROLES,
    _distinct_of,
    carriers,
)
from tests.support.detector_fixtures import headline_negatives, labelled_rows, set_body, version
from tests.support.publish_fixtures import store_token
from tests.support.send_fixtures import API, NAMESPACE, SendDriver, repos_for, start_send

REGISTER = "/api/v1/probe-monitors/datasets"
SPANS = "r_ev_spans"
OTHER = "r_body_spans"
ROLES = {**BEHAVIOUR_ROLES, SPANS: "metadata", OTHER: "metadata"}
LISTED = [
    {"column": SPANS, "text_column": "text", "unit": "code_points", "rule_set": "ev@1"},
    {"column": OTHER, "text_column": "body", "unit": "code_points", "rule_set": "body@1"},
]


def with_spans(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The first word of each positive row is its evidence (offsets into ``text``); every third negative
    carries the same surface form, as a surface-only hard negative does."""
    out = []
    for i, row in enumerate(rows):
        first = str(row["text"]).split(" ", 1)[0]
        marked = row["label"] == "humorous" or i % 3 == 0
        out.append({**row, SPANS: json.dumps([[0, len(first)]]) if marked else "[]", OTHER: "[]"})
    return out


@pytest.fixture
def spans_listed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(evidence_spans, "span_outputs", lambda session, version: list(LISTED))


def _made(splits: dict[str, list[dict[str, Any]]], name: str, **kw: Any) -> str:
    """G11's behaviour version, with the span columns as metadata."""
    made = version(splits, dataset_name=name, roles=ROLES, **kw)
    with get_sync_engine().begin() as conn:
        conn.execute(text("ALTER TABLE dw_versions DISABLE TRIGGER dw_versions_immutable"))
        conn.execute(
            text("UPDATE dw_versions SET behaviour_configuration = '{\"a\": 1}' WHERE id = :v"),
            {"v": made},
        )
        conn.execute(text("ALTER TABLE dw_versions ENABLE TRIGGER dw_versions_immutable"))
    return made


def versions() -> dict[str, str]:
    train = _made(
        {
            "train": with_spans(carriers(labelled_rows(200, seed=1, tag="tr"), "tr")),
            "test": with_spans(carriers(labelled_rows(60, seed=2, tag="te"), "te")),
        },
        "pilot-main",
        held_out={"test": True},
    )
    ood = _made(
        {"test": with_spans(carriers(labelled_rows(120, seed=3, tag="ood", pairs=True), "ood"))},
        "pilot-ood",
    )
    cal = version({"train": headline_negatives(150, seed=5)}, dataset_name="plain-negatives")
    return {"train": train, "ood": ood, "cal": cal}


async def _ready(client: httpx.AsyncClient, sender: SendDriver) -> tuple[str, dict[str, str]]:
    await store_token(client)
    made = versions()
    body = set_body(made)
    for role in body["roles"][:3]:
        # the pack's labels are derived from the span column (derive_labels' scan_column), and it says so
        role["label_source_columns"] = [SPANS]
    created = await client.post(f"{API}/detector-sets", json=body)
    assert created.status_code == 201, created.text
    set_id = created.json()["id"]
    first = await client.post(f"{API}/detector-sets/{set_id}/send", json={"namespace": NAMESPACE})
    assert first.status_code == 409, first.text
    sender.publisher.run_publish_tasks()
    repos = repos_for(made)
    sender.seed_values(repos, made)
    for key, split in (("train", "train"), ("train", "test"), ("ood", "test"), ("cal", "train")):
        sender.mistudio.split_distinct[(repos[made[key]], split)] = _distinct_of(made[key], split)
    return set_id, made


def _pushed_listing(send_id: str, version_id: str) -> list[dict[str, Any]] | None:
    with sync_session_factory()() as db:
        publish = (
            db.query(Publish)
            .filter(Publish.send_id == send_id, Publish.version_id == version_id)
            .one()
        )
        return ((publish.published_manifest or {}).get("extensions") or {}).get("evidence_spans")


async def test_every_behaviour_role_declares_the_span_column_over_its_input(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    sender: SendDriver,
    spans_listed: None,
) -> None:
    sender.mistudio.serves_span_column = True
    set_id, made = await _ready(client, sender)
    started = await start_send(client, set_id)
    assert sender.run(started["job_id"])["status"] == "completed", sender.send(
        started["send_id"]
    ).error

    regs = sender.mistudio.calls("POST", REGISTER)
    assert len(regs) == 4, "one registration per role"
    declared = {
        (c.body["role"], c.body["split"], c.body.get("distribution")): c.body.get("span_column")
        for c in regs
    }
    assert sorted(v for v in declared.values() if v) == [SPANS] * 3, declared
    assert [v for (role, *_), v in declared.items() if role == "calibration"] == [
        None
    ], "a version that is not a behaviour build lists nothing"
    assert _pushed_listing(started["send_id"], made["train"]) == LISTED
    train_view = next(v for v in sender.mistudio.views.values() if v["role"] == "train")
    assert train_view["span_column"] == SPANS
    got = (await client.get(f"{API}/detector-sends/{started['send_id']}")).json()
    train_id = next(r["id"] for r in got["snapshot"]["roles"] if r["role"] == "train")
    assert got["span_columns"][train_id] == {
        "declared": SPANS,
        "left_out": {OTHER: "its offsets index 'body', and this role's input column is 'text'"},
    }


async def test_a_miWorkbench_that_does_not_serve_it_gets_no_key_and_the_step_says_why(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    sender: SendDriver,
    spans_listed: None,
) -> None:
    assert sender.mistudio.serves_span_column is False
    set_id, _ = await _ready(client, sender)
    started = await start_send(client, set_id)
    assert sender.run(started["job_id"])["status"] == "completed", sender.send(
        started["send_id"]
    ).error
    regs = sender.mistudio.calls("POST", REGISTER)
    assert len(regs) == 4 and all("span_column" not in c.body for c in regs)
    notes = [
        s.behaviour["span_column"]
        for (kind, _), s in sender.steps(started["send_id"]).items()
        if kind == "register" and s.behaviour and "span_column" in s.behaviour
    ]
    assert len(notes) == 3 and all("not served" in n for n in notes)


async def test_a_view_registered_without_spans_is_registered_again_once_miWorkbench_takes_them(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    sender: SendDriver,
    spans_listed: None,
) -> None:
    """A reused view keeps no span column (there is no route that adds one), so a send that can carry
    them registers the behaviour roles afresh; the plain role, with none to carry, is reused. A third
    send reuses all four."""
    set_id, _ = await _ready(client, sender)
    first = await start_send(client, set_id)
    assert sender.run(first["job_id"])["status"] == "completed"
    fake = sender.mistudio
    assert fake.count("POST", REGISTER) == 4
    fake.serves_span_column = True
    capabilities.clear_cache()
    again = await start_send(client, set_id)
    assert sender.run(again["job_id"])["status"] == "completed", sender.send(again["send_id"]).error
    fresh = fake.calls("POST", REGISTER)[4:]
    assert len(fresh) == 3 and all(c.body["span_column"] == SPANS for c in fresh)
    third = await start_send(client, set_id)
    assert sender.run(third["job_id"])["status"] == "completed"
    assert fake.count("POST", REGISTER) == 7, "registered with spans once: reused from then on"
