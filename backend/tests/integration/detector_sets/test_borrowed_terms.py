"""Review M1 (2026-11-08) of ENH-006 G21: a borrower is compared with what the sibling's SEND
registered, not with the sibling's row now.

Reproduced defect: send the sibling, change its positive meaning (or re-read it to a new definition
version), and a borrower carrying the NEW terms passed D-1 and lent views registered under the OLD
ones; D-1's own advice ("re-read one") produced exactly that send. Now D-1 and the send's own guard
both refuse and say to re-send the sibling; once re-sent, the borrower passes. Also: only a send to
the miWorkbench configured now lends views (minor 1), the LATEST completed send is the one read, and an
extra evaluation role is borrowed too (surviving mutations 3 and 4).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from src.core.config import get_settings
from src.core.database import sync_session_factory
from src.models.detector_set import DetectorSet
from src.services.detector_sets import send_service
from src.services.detector_sets.errors import DetectorSetError
from tests.integration.detector_sets.test_borrowed_roles import (
    KEY,
    MEANING,
    S,
    checks_of,
    d,
    sent_sibling,
    train_only,
)
from tests.support.detector_fixtures import labelled_rows, version
from tests.support.fake_miworkbench import catalogue_head
from tests.support.send_fixtures import API, NAMESPACE, SendDriver, ready_set, repos_for, start_send

ROLE_KEYS = ("role", "version_id", "split", "input_column", "label_column", "label_mapping")


def guard_refusal(set_id: str) -> DetectorSetError | None:
    """The send's own guard behind D-1 (``send_service.borrowed_views``)."""
    with sync_session_factory()() as db:
        row = db.get(DetectorSet, set_id)
        assert row is not None
        try:
            send_service.borrowed_views(db, row)
        except DetectorSetError as exc:
            return exc
    return None


def problems(result: dict[str, Any]) -> list[str]:
    return list(d(result, "D-1")["details"].get("problems") or [])


async def resend(client: httpx.AsyncClient, sender: SendDriver, set_id: str) -> str:
    started = await start_send(client, set_id)
    assert sender.run(started["job_id"])["status"] == "completed"
    return str(started["send_id"])


def role_specs(roles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in roles:
        spec = {k: r[k] for k in ROLE_KEYS}
        for k in ("pair_column", "negatives_basis"):
            if r.get(k) is not None:
                spec[k] = r[k]
        out.append(spec)
    return out


async def test_a_meaning_changed_after_the_send_is_refused_until_the_sibling_is_resent(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    sibling, first = await sent_sibling(client, sender)
    changed = await client.patch(f"{S}/{sibling}", json={"positive_meaning": "a new meaning"})
    assert changed.status_code == 200, changed.text
    train_b = version({"train": labelled_rows(200, seed=31, tag="armh")}, dataset_name="arm-h")
    made = await client.post(
        S, json=train_only("arm-new-meaning", train_b, sibling, positive_meaning="a new meaning")
    )
    assert made.status_code == 201, made.text
    arm = made.json()["id"]
    d1 = d(await checks_of(client, arm), "D-1")
    assert d1["outcome"] == "refused", d1
    found = [p for p in problems(await checks_of(client, arm)) if "positive meanings differ" in p]
    assert found and MEANING in found[0] and "re-send the sibling" in found[0], found
    refused = guard_refusal(arm)
    assert refused is not None and refused.code == "send_refused"
    assert refused.details["sibling_send_id"] == first

    second = await resend(client, sender, sibling)
    result = await checks_of(client, arm)
    assert d(result, "D-1")["outcome"] == "green", problems(result)
    assert d(result, "D-1")["details"]["sibling_send_id"] == second
    assert guard_refusal(arm) is None


async def test_a_reread_after_the_send_is_refused_until_the_sibling_is_resent(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    """The path D-1's old advice ("re-read one") led to."""
    sender.mistudio.behaviours[KEY] = catalogue_head(KEY, version=1, positive_meaning=MEANING)
    sibling, _first = await sent_sibling(client, sender)
    assert (await client.get(f"{S}/{sibling}")).json()["behaviour_definition"][
        "definition_version"
    ] == 1
    sender.mistudio.behaviours[KEY] = catalogue_head(KEY, version=2, positive_meaning=MEANING)
    reread = await client.post(f"{S}/{sibling}/behaviour-definition/reread")
    assert reread.status_code == 200, reread.text
    train_b = version({"train": labelled_rows(200, seed=32, tag="armi")}, dataset_name="arm-i")
    made = (await client.post(S, json=train_only("arm-v2", train_b, sibling))).json()
    assert made["behaviour_definition"]["definition_version"] == 2
    found = [p for p in problems(await checks_of(client, made["id"])) if "definitions differ" in p]
    assert found and "v1" in found[0] and "re-send the sibling" in found[0], found
    refused = guard_refusal(made["id"])
    assert refused is not None and any("definitions differ" in x for x in refused.details["differ"])

    await resend(client, sender, sibling)
    result = await checks_of(client, made["id"])
    assert d(result, "D-1")["outcome"] == "green", problems(result)
    assert guard_refusal(made["id"]) is None


async def test_a_send_to_another_miWorkbench_lends_nothing(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    sender: SendDriver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sibling, _ = await sent_sibling(client, sender)
    train_b = version({"train": labelled_rows(200, seed=33, tag="armj")}, dataset_name="arm-j")
    made = (await client.post(S, json=train_only("arm-elsewhere", train_b, sibling))).json()
    assert d(await checks_of(client, made["id"]), "D-1")["outcome"] == "green"
    monkeypatch.setattr(get_settings(), "mistudio_base_url", "http://another-mistudio.test")
    found = problems(await checks_of(client, made["id"]))
    assert any("no completed send to http://another-mistudio.test" in p for p in found), found
    assert guard_refusal(made["id"]) is not None


async def test_the_latest_send_lends_and_an_extra_evaluation_role_is_borrowed(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    sibling, _versions, _repos = await ready_set(client, sender, {"behaviour_key": KEY})
    first = await resend(client, sender, sibling)
    out = (await client.get(f"{S}/{sibling}")).json()
    specs = role_specs(out["roles"])
    ood = next(s for s in specs if s["role"] == "ood_eval")
    extra = {k: v for k, v in ood.items() if k != "pair_column"} | {"role": "extra_eval"}
    changed = await client.patch(f"{S}/{sibling}", json={"roles": [*specs, extra]})
    assert changed.status_code == 200, changed.text
    second = await resend(client, sender, sibling)
    assert second != first

    train_b = version({"train": labelled_rows(200, seed=34, tag="armk")}, dataset_name="arm-k")
    made = (await client.post(S, json=train_only("arm-extra-eval", train_b, sibling))).json()
    assert sorted(r["role"] for r in made["borrowed_roles"]) == [
        "calibration_negatives",
        "extra_eval",
        "id_test",
        "ood_eval",
    ]
    result = await checks_of(client, made["id"])
    d1 = d(result, "D-1")
    assert d1["outcome"] == "green", problems(result)
    assert d1["details"]["sibling_send_id"] == second, "the latest completed send lends"
    assert len(d1["details"]["borrowed_views"]) == 4

    first_try = await client.post(f"{S}/{made['id']}/send", json={"namespace": NAMESPACE})
    assert first_try.status_code == 409, first_try.text
    sender.publisher.run_publish_tasks()
    repo_b = repos_for({"train": train_b})[train_b]
    sender.mistudio.values[(repo_b, "train")] = {"humorous": 100, "not_humorous": 100}
    started = await start_send(client, made["id"])
    assert sender.run(started["job_id"])["status"] == "completed"
    got = (await client.get(f"{API}/detector-sends/{started['send_id']}")).json()
    assert len(got["run_request"]["eval_dataset_ids"]) == 3, got["run_request"]
    assert {v["role"] for v in got["borrowed_views"]} >= {"extra_eval"}


async def test_a_sibling_send_without_a_d10_record_is_refused(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    """Re-review minor 3: a send made before D-10 recorded the set's definition says nothing about
    the version its views were registered under, so it lends nothing until the sibling is re-sent.
    """
    import json

    from sqlalchemy import text

    from src.core.database import get_sync_engine

    sibling, first = await sent_sibling(client, sender)
    with sync_session_factory()() as db:
        checks = db.execute(
            text("SELECT checks FROM dw_detector_sends WHERE id = :i"), {"i": first}
        ).scalar_one()
    older = [c for c in checks if c.get("code") != "D-10"]  # as a pre-017 send recorded them
    with get_sync_engine().begin() as conn:
        # A send's checks are written once by design; the test plays a send from before D-10.
        conn.execute(text("ALTER TABLE dw_detector_sends DISABLE TRIGGER dw_detector_sends_frozen"))
        conn.execute(
            text("UPDATE dw_detector_sends SET checks = CAST(:c AS jsonb) WHERE id = :i"),
            {"c": json.dumps(older), "i": first},
        )
        conn.execute(text("ALTER TABLE dw_detector_sends ENABLE TRIGGER dw_detector_sends_frozen"))
    train_b = version({"train": labelled_rows(200, seed=35, tag="arml")}, dataset_name="arm-l")
    made = (await client.post(S, json=train_only("arm-pre-d10", train_b, sibling))).json()
    found = problems(await checks_of(client, made["id"]))
    assert any("recorded no definition version" in p for p in found), found
    refused = guard_refusal(made["id"])
    assert refused is not None
    assert any("recorded no definition version" in x for x in refused.details["differ"])
