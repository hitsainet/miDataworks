"""A link reads miStudio's render form at link time and states it (2026-10-08 production gate).

The fake miStudio serves the humor probe's REAL 2026-10-07 report (``test_reproduction_links``'s
``real_mistudio``) with its ``probe`` block's render fields replaced by what production serves on
2026-10-08: ``report_pm_1b1f8c50d6d7_2026-10-08.json`` for the served form (the production humor
probe), or a scripted explicit false. The absent case is ``test_reproduction_links`` itself.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from src.core.config import get_settings
from src.core.database import sync_session_factory
from src.services.detector_sets import reproduction_links as rl
from tests.integration.detector_sets.test_probe_verdict_runs import (
    body,
    millm,  # noqa: F401
    plan,
    rows,
)
from tests.integration.detector_sets.test_reproduction_links import (  # noqa: F401
    SOURCE,
    _link_row,
    gate_texts,
    imported,
    labelled_version,
    link_body,
    real_mistudio,
)
from tests.support.fake_mistudio import BASE, FIXTURES, FakeMiStudio
from tests.support.labeling_fixtures import Labeling, labeling, make_version  # noqa: F401
from tests.support.send_fixtures import API

SERVED_PROBE = json.loads((FIXTURES / "report_pm_1b1f8c50d6d7_2026-10-08.json").read_text())[
    "probe"
]
SERVED_RUN_ENV = json.loads((FIXTURES / "run_pmr_59980a0fb155.json").read_text())["environment"]


def studio_with(monkeypatch: pytest.MonkeyPatch, render_form: Any, served: bool) -> FakeMiStudio:
    from src.clients import mistudio_client

    fake = real_mistudio()
    fake.reports[SOURCE]["probe"]["render_form"] = render_form
    fake.reports[SOURCE]["probe"]["render_served"] = served
    monkeypatch.setattr(mistudio_client, "TRANSPORT", fake.transport())
    monkeypatch.setattr(get_settings(), "mistudio_base_url", BASE)
    return fake


async def test_a_served_probe_links_equal_by_render_rule_through_plan_and_run(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    imported: Labeling,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert SERVED_PROBE["render_form"] == rl.SERVED_RENDER_FORM
    studio = studio_with(monkeypatch, SERVED_PROBE["render_form"], SERVED_PROBE["render_served"])
    version_id = labelled_version(data_dir, gate_texts())
    made = await client.post(f"{API}/reproduction-links", json=link_body(version_id))
    assert made.status_code == 201, made.text
    form = made.json()["scoring_form"]
    assert form["agreement"] == "equal_by_render_rule"
    assert form["token_ids_compared"] is False and "not verified by token ids" in form["reason"]
    assert form["mistudio"]["render_form"] == rl.SERVED_RENDER_FORM
    assert form["mistudio"]["render_form_source"] == "probe"
    assert form["mistudio"]["render_served"] is True
    assert form["millm"]["last_roles"] == {"user": 60, "assistant": 0, "other": 0}
    assert "no generation prompt" not in json.dumps(form)
    # Read-only: every call to miStudio was a GET.
    assert {r.method for r in studio.requests} == {"GET"}

    to_label = make_version(data_dir, rows(10))
    planned = await plan(client, body(to_label))
    assert planned.status_code == 200, planned.text
    assert planned.json()["reproduction"]["scoring_form"] == form

    started = await client.post(f"{API}/label-runs", json=body(to_label))
    assert started.status_code == 201, started.text
    final = imported.run_until_done(started.json()["id"])
    assert final.state == "completed", final.error
    assert final.endpoint_snapshot["reproduction"]["scoring_form"] == form


async def test_an_explicit_false_on_plain_text_differs_and_is_stored_as_recorded(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    imported: Labeling,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = {"generation_prompt": False, "add_special_tokens": False}
    studio_with(monkeypatch, recorded, False)
    made = await client.post(
        f"{API}/reproduction-links", json=link_body(labelled_version(data_dir, gate_texts()))
    )
    assert made.status_code == 201, made.text
    form = made.json()["scoring_form"]
    assert form["agreement"] == "differs" and "generation_prompt is False" in form["reason"]
    assert form["mistudio"]["render_form"] == recorded
    assert form["mistudio"]["render_form_recorded"] is True
    assert form["mistudio"]["render_served"] is False


async def test_a_null_render_form_is_not_recorded_and_differs(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    imported: Labeling,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    studio_with(monkeypatch, None, False)  # what production serves for pm_c99519a98e08 today
    made = await client.post(
        f"{API}/reproduction-links", json=link_body(labelled_version(data_dir, gate_texts()))
    )
    assert made.status_code == 201, made.text
    form = made.json()["scoring_form"]
    assert form["agreement"] == "differs"
    assert (
        form["mistudio"]["render_form_recorded"] is False
        and form["mistudio"]["render_form"] is None
    )
    assert rl.NOT_RECORDED in form["mistudio"]["described_as"]


def _stored(link_id: str, scoring_form: dict[str, Any], evidence: dict[str, Any]) -> None:
    with sync_session_factory()() as db:
        db.execute(
            text("ALTER TABLE dw_reproduction_links DISABLE TRIGGER dw_reproduction_links_frozen")
        )
        db.execute(
            text(
                "UPDATE dw_reproduction_links SET scoring_form = CAST(:f AS JSONB), "
                "evidence = CAST(:e AS JSONB) WHERE id = :i"
            ),
            {"f": json.dumps(scoring_form), "e": json.dumps(evidence), "i": link_id},
        )
        db.execute(
            text("ALTER TABLE dw_reproduction_links ENABLE TRIGGER dw_reproduction_links_frozen")
        )
        db.commit()


LEGACY = {
    "mistudio": {
        "input_kinds": {"plain": 6600},
        "scope": "all",
        "described_as": "each plain-text row parsed as one user turn and rendered with the "
        "model's chat template, no generation prompt (miStudio probe_monitor_inputs.parse_input, "
        "probe_monitor_render.render_messages at c829a2cc)",
    },
    "millm": {"input_form": "text", "described_as": "one user turn"},
    "agreement": "not_verified",
    "reason": "Both read one user turn per row.",
}


async def test_an_old_link_keeps_what_it_stored_with_a_note_only_when_known_wrong(
    client: httpx.AsyncClient, data_dir: Path, clean_db: None
) -> None:
    version_id = labelled_version(data_dir, gate_texts())
    wrong = _link_row(version_id, "ood_eval", 0, 0.9)
    right = _link_row(version_id, "id_test", 1, 0.9)
    _stored(wrong, LEGACY, {"run_environment": SERVED_RUN_ENV})
    _stored(right, LEGACY, {"run_environment": {"scope": "all"}})
    got = await client.get(f"{API}/reproduction-links")
    assert got.status_code == 200, got.text
    by_id = {link["id"]: link["scoring_form"] for link in got.json()["items"]}
    assert by_id[right] == LEGACY
    assert {k: v for k, v in by_id[wrong].items() if k != "note"} == LEGACY
    assert "render_form" in by_id[wrong]["note"] and "wrong" in by_id[wrong]["note"]
    with sync_session_factory()() as db:
        stored = db.execute(
            text("SELECT scoring_form FROM dw_reproduction_links WHERE id = :i"), {"i": wrong}
        ).scalar_one()
    assert stored == LEGACY  # history is not rewritten


async def test_the_plan_of_an_old_link_carries_the_note_and_the_stored_text(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    imported: Labeling,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gate's target reads the link through ``scoring_form_out`` too (control W4): a plan made
    from a pre-fix link shows its stored text and the note, as the link list does."""
    studio_with(monkeypatch, None, False)
    made = await client.post(
        f"{API}/reproduction-links", json=link_body(labelled_version(data_dir, gate_texts()))
    )
    assert made.status_code == 201, made.text
    _stored(made.json()["id"], LEGACY, {"run_environment": SERVED_RUN_ENV})
    planned = await plan(client, body(make_version(data_dir, rows(10))))
    assert planned.status_code == 200, planned.text
    form = planned.json()["reproduction"]["scoring_form"]
    assert {k: v for k, v in form.items() if k != "note"} == LEGACY
    assert "render_form" in form["note"]
