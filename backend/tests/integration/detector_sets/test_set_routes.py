"""Detector sets through REST against real versions, with feature 004's REAL audit and leakage
check (FTASKS 4.6; US-1 to US-3; coordinator request 2026-10-07: D-3 and D-4 end to end).

Nothing stubs 004 here: ``evaluate_warnings`` audits the training and in-distribution splits, and
``check_leakage`` compares every role. Fixtures differ in what each test checks: a shortcut version
has a ``format`` column equal to the label; a leaking set binds the same split as two roles.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.support.detector_fixtures import humor_versions, set_body

API = "/api/v1"


def outcome(checks: dict[str, Any], code: str) -> dict[str, Any]:
    return next(o for o in checks["outcomes"] if o["code"] == code)


async def create(client: httpx.AsyncClient, body: dict[str, Any]) -> dict[str, Any]:
    response = await client.post(f"{API}/detector-sets", json=body)
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()
    return data


async def checks_of(client: httpx.AsyncClient, set_id: str) -> dict[str, Any]:
    response = await client.post(f"{API}/detector-sets/{set_id}/checks")
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


async def test_a_clean_set_is_created_checked_and_allowed(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    versions = humor_versions()
    created = await create(client, set_body(versions))
    assert created["created_by"] == operator_name
    assert created["role_counts"] == {
        "train": 1,
        "id_test": 1,
        "ood_eval": 1,
        "calibration_negatives": 1,
    }
    ood = next(r for r in created["roles"] if r["role"] == "ood_eval")
    assert created["monitored_ref"] == {"kind": "role", "role_id": ood["id"]}  # T-45 default
    checks = await checks_of(client, created["id"])
    by_code = {o["code"]: o["outcome"] for o in checks["outcomes"]}
    assert by_code["D-3"] == "green", outcome(checks, "D-3")
    assert by_code["D-4"] == "green", outcome(checks, "D-4")
    assert checks["send_allowed"], checks["first_refusal"]
    train = next(r for r in created["roles"] if r["role"] == "train")
    # the mapping is checked against the split's REAL values, with counts per role
    assert checks["label_values"][train["id"]] == {"humorous": 100, "not_humorous": 100}
    assert checks["expected_counts"][train["id"]]["positive"] == 100
    # both profiles, with n; the calibration negatives' finest FPR is 1/n
    cal = next(r for r in created["roles"] if r["role"] == "calibration_negatives")
    assert checks["profiles"][cal["id"]]["n"] == 150
    assert checks["profiles"][cal["id"]]["finest_fpr"] == pytest.approx(1 / 150)
    assert checks["monitored"]["profile"]["n"] == 120
    # words as well as characters (FR-009.9); headline negatives run 9 words each
    assert checks["profiles_words"][cal["id"]]["unit"] == "words"
    assert checks["profiles_words"][cal["id"]]["quantiles"]["p50"] == 9


async def test_a_shortcut_warning_refuses_the_send(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    created = await create(client, set_body(humor_versions(shortcut=True)))
    checks = await checks_of(client, created["id"])
    d3 = outcome(checks, "D-3")
    assert d3["outcome"] == "refused", d3
    assert any(w["column"] == "format" for w in d3["details"]["warnings"]), d3
    assert not checks["send_allowed"]


async def test_leakage_across_roles_refuses_the_send(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    versions = humor_versions()
    body = set_body(versions)
    # the OOD role binds the training split itself: every OOD row is also a training row
    body["roles"][2] = {**body["roles"][2], "version_id": versions["train"], "split": "train"}
    created = await create(client, body)
    checks = await checks_of(client, created["id"])
    d4 = outcome(checks, "D-4")
    assert d4["outcome"] == "refused", d4
    assert "training rows" in d4["reason"] and "out-of-distribution" in d4["reason"], d4
    assert not checks["send_allowed"]


async def test_a_length_mismatch_is_a_note_and_does_not_block(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    created = await create(client, set_body(humor_versions(chat_calibration=True)))
    checks = await checks_of(client, created["id"])
    d5 = outcome(checks, "D-5")
    assert d5["outcome"] == "note", d5
    assert d5["details"]["figure"] < 0.5
    assert checks["send_allowed"], checks["first_refusal"]


async def test_an_unmapped_value_and_an_incomplete_version_are_refused(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    versions = humor_versions()
    body = set_body(versions)
    body["roles"][0]["label_mapping"] = {"humorous": "positive"}
    created = await create(client, body)
    checks = await checks_of(client, created["id"])
    d2 = outcome(checks, "D-2")
    assert d2["outcome"] == "refused" and "not_humorous" in d2["reason"], d2

    bad = set_body(versions, name="humor-bad")
    bad["roles"][0]["version_id"] = "00000000-0000-0000-0000-000000000000"
    response = await client.post(f"{API}/detector-sets", json=bad)
    assert response.status_code == 409 and response.json()["error"]["code"] == "version_incomplete"


async def test_a_missing_calibration_record_refuses(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-7: a labeler bound to a role's version with no calibration record refuses (P-02)."""
    from src.core.database import sync_session_factory
    from src.models import Version
    from tests.support.calibration_fixtures import make_run

    versions = humor_versions()
    run = make_run(versions["ood"], {})
    with sync_session_factory()() as db:
        v = db.get(Version, versions["ood"])
        assert v is not None
        from sqlalchemy import text

        db.execute(text("ALTER TABLE dw_versions DISABLE TRIGGER dw_versions_immutable"))
        v.bindings = [{"kind": "label_run", "id": run.id}]
        db.commit()
        db.execute(text("ALTER TABLE dw_versions ENABLE TRIGGER dw_versions_immutable"))
        db.commit()
    created = await create(client, set_body(versions))
    d7 = outcome(await checks_of(client, created["id"]), "D-7")
    assert d7["outcome"] == "refused", d7
    assert d7["details"]["labelers"][0]["verdict"] == "none"


async def test_name_taken_second_train_and_archive(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    versions = humor_versions()
    created = await create(client, set_body(versions))
    again = await client.post(f"{API}/detector-sets", json=set_body(versions))
    assert again.status_code == 409 and again.json()["error"]["code"] == "name_taken"
    two_trains = set_body(versions, name="two-trains")
    two_trains["roles"].append(dict(two_trains["roles"][0]))
    refused = await client.post(f"{API}/detector-sets", json=two_trains)
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "roles_incomplete"
    archived = await client.post(f"{API}/detector-sets/{created['id']}/archive")
    assert archived.status_code == 200 and archived.json()["archived"] is True
    listed = (await client.get(f"{API}/detector-sets", params={"archived": True})).json()
    assert [s["id"] for s in listed["items"]] == [created["id"]]


async def test_a_role_bound_version_cannot_be_deleted(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    """002's delete refusal reads 009's membership (FTASKS 11.1)."""
    versions = humor_versions()
    await create(client, set_body(versions))
    response = await client.request(
        "DELETE", f"{API}/versions/{versions['cal']}", json={"reason": "cleanup"}
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "version_in_detector_set"


async def test_a_body_who_field_is_refused(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    body = set_body(humor_versions())
    body["created_by"] = "someone else"
    response = await client.post(f"{API}/detector-sets", json=body)
    assert response.status_code == 422
