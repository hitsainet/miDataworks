"""The three detector-set check defects found on production on 2026-10-07, reproduced on a version
in Humicroedit's REAL shape (``tests/support/detector_fixtures.py``, "Humicroedit").

Production set ``dts_9bc3601e674ad12d49469598`` (evidence: ``set_create.json``, ``checks1.json``):

1. D-3 refused on ``meanGrade`` (1.000) and ``grades`` (0.994), the columns ``human_label`` is
   DEFINED from. A role now declares ``label_source_columns``; 004 excludes them and D-3 reports
   them as excluded, never silently, while an undeclared column is still audited.
2. Human-graded calibration negatives had to claim ``assumed_negative``.
3. Null labels were dropped from the label values, the expected counts and D-2.

Nothing stubs 004: D-3 runs its real audit over the training and in-distribution splits.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from src.core.database import sync_session_factory
from tests.support.curation_fixtures import StepFixture
from tests.support.detector_fixtures import (
    HUMAN_BASIS,
    HUMICROEDIT_SHAPE,
    humicroedit_body,
    humicroedit_version,
    labelled_rows,
    version,
)

API = "/api/v1"
SOURCES = ["meanGrade", "grades"]


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


def role_of(created: dict[str, Any], kind: str) -> dict[str, Any]:
    return next(r for r in created["roles"] if r["role"] == kind)


# --- defect 1: the label's own source columns -------------------------------------------------


async def test_undeclared_label_sources_refuse_as_production_did(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    created = await create(client, humicroedit_body(humicroedit_version()))
    d3 = outcome(await checks_of(client, created["id"]), "D-3")
    assert d3["outcome"] == "refused", d3
    flagged = {w["column"] for w in d3["details"]["warnings"]}
    assert flagged == {"meanGrade", "grades"}, d3
    assert d3["details"]["excluded"] == []


async def test_declared_label_sources_are_excluded_and_reported(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    created = await create(client, humicroedit_body(humicroedit_version(), label_sources=SOURCES))
    assert role_of(created, "train")["label_source_columns"] == SOURCES
    d3 = outcome(await checks_of(client, created["id"]), "D-3")
    assert d3["outcome"] == "green", d3
    excluded = {e["column"]: e for e in d3["details"]["excluded"]}
    assert set(excluded) == {"meanGrade", "grades"}, d3
    for e in excluded.values():
        assert e["reason"] == "label_source"
        # who declared it: both audited roles, by their view names
        assert "training rows" in e["declared_by"]
        assert "in-distribution test" in e["declared_by"]
    # REPORTED, not silent: the reason names both columns
    assert "'meanGrade'" in d3["reason"] and "'grades'" in d3["reason"], d3["reason"]
    assert "Not audited" in d3["reason"]


async def test_an_undeclared_source_column_is_still_audited(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    created = await create(
        client, humicroedit_body(humicroedit_version(), label_sources=["meanGrade"])
    )
    d3 = outcome(await checks_of(client, created["id"]), "D-3")
    assert d3["outcome"] == "refused", d3
    assert {w["column"] for w in d3["details"]["warnings"]} == {"grades"}, d3
    assert [e["column"] for e in d3["details"]["excluded"]] == ["meanGrade"]
    assert "'meanGrade'" in d3["reason"]


async def test_a_declaration_on_one_audited_role_names_that_role_only(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    body = humicroedit_body(humicroedit_version(), label_sources=SOURCES)
    body["roles"][1]["label_source_columns"] = []  # id_test declares nothing
    created = await create(client, body)
    d3 = outcome(await checks_of(client, created["id"]), "D-3")
    assert d3["outcome"] == "green", d3
    for e in d3["details"]["excluded"]:
        assert "training rows" in e["declared_by"]
        assert "in-distribution test" not in e["declared_by"]


async def test_label_source_declarations_are_validated(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    v = humicroedit_version()
    cases = {
        "no_such_column": ["meanGrade", "nope"],
        "input_column": ["text"],
        "label_column": ["human_label"],
        "twice": ["grades", "grades"],
        "system_column": ["_dw_row_key"],
    }
    for case, declared in cases.items():
        body = humicroedit_body(v, name=f"hum-{case.replace('_', '-')}", label_sources=declared)
        response = await client.post(f"{API}/detector-sets", json=body)
        assert response.status_code == 422, (case, response.text)
        assert response.json()["error"]["code"] == "role_invalid", (case, response.text)
    # the same validation on update
    created = await create(client, humicroedit_body(v, name="hum-update"))
    roles = humicroedit_body(v, label_sources=["text"])["roles"]
    response = await client.patch(f"{API}/detector-sets/{created['id']}", json={"roles": roles})
    assert response.status_code == 422, response.text
    roles = humicroedit_body(v, label_sources=SOURCES)["roles"]
    response = await client.patch(f"{API}/detector-sets/{created['id']}", json={"roles": roles})
    assert response.status_code == 200, response.text
    got = (await client.get(f"{API}/detector-sets/{created['id']}")).json()
    assert role_of(got, "id_test")["label_source_columns"] == SOURCES


async def test_the_send_snapshot_records_the_declaration(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    from src.core.database import sync_session_factory
    from src.services.detector_sets import send_service, set_service

    created = await create(client, humicroedit_body(humicroedit_version(), label_sources=SOURCES))
    with sync_session_factory()() as db:
        row = set_service.get_set(db, created["id"])
        roles = set_service.roles_of(db, row.id)
        snap = send_service.snapshot_of(db, row, roles, {}, "private")
    by_role = {r["role"]: r for r in snap["roles"]}
    assert by_role["train"]["label_source_columns"] == SOURCES
    assert by_role["id_test"]["label_source_columns"] == SOURCES
    assert by_role["calibration_negatives"]["label_source_columns"] == []


# --- defect 2: human-labelled calibration negatives ---------------------------------------------


async def test_a_human_labelled_basis_is_reported_without_the_assumed_caveat(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    created = await create(client, humicroedit_body(humicroedit_version(), basis=HUMAN_BASIS))
    cal = role_of(created, "calibration_negatives")
    assert cal["negatives_basis"] == HUMAN_BASIS
    d8 = outcome(await checks_of(client, created["id"]), "D-8")
    assert d8["outcome"] == "green", d8
    assert "human-labelled" in d8["reason"]
    assert "'human_label'" in d8["reason"] and "'0'" in d8["reason"]
    assert HUMAN_BASIS["labelled_by"] in d8["reason"]
    assert "assumed" not in d8["reason"].lower()
    assert d8["details"]["basis"]["kind"] == "human_labelled"
    # the assumed basis production had to use is still a note
    other = await create(
        client,
        humicroedit_body(humicroedit_version("humicroedit-2"), name="hum-assumed", basis=None),
    )
    assert outcome(await checks_of(client, other["id"]), "D-8")["outcome"] == "note"


async def test_a_human_labelled_basis_must_describe_its_role(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    v = humicroedit_version()
    bad = {
        "wrong-column": {**HUMAN_BASIS, "label_column": "kind"},
        "wrong-values": {**HUMAN_BASIS, "negative_values": ["0", "1"]},
        "too-few-values": {**HUMAN_BASIS, "negative_values": ["1"]},
    }
    for name, basis in bad.items():
        response = await client.post(
            f"{API}/detector-sets", json=humicroedit_body(v, name=f"hum-{name}", basis=basis)
        )
        assert response.status_code == 422, (name, response.text)
        assert response.json()["error"]["code"] == "role_invalid", (name, response.text)
    # incomplete or mixed bases are refused by the request model
    for name, basis in {
        "no-labelled-by": {k: v for k, v in HUMAN_BASIS.items() if k != "labelled_by"},
        "no-values": {**HUMAN_BASIS, "negative_values": []},
        "no-rule": {k: v for k, v in HUMAN_BASIS.items() if k != "rule"},
        "with-labeler": {**HUMAN_BASIS, "labeler_identity_hash": "a" * 64},
        "assumed-with-values": {"kind": "assumed_negative", "negative_values": ["0"]},
    }.items():
        response = await client.post(
            f"{API}/detector-sets", json=humicroedit_body(v, name=f"hum-{name}", basis=basis)
        )
        assert response.status_code == 422, (name, response.text)


async def test_a_labeler_written_label_column_cannot_claim_human_labels(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    labelled = version(
        {"train": labelled_rows(40, seed=9, tag="lab")},
        dataset_name="model-labelled",
        steps=[StepFixture("threshold_labeler")],
    )
    body = humicroedit_body(humicroedit_version(), name="hum-model-labelled")
    body["roles"][2] = {
        "role": "calibration_negatives",
        "version_id": labelled,
        "split": "train",
        "input_column": "text",
        "label_column": "label",
        "label_mapping": {"not_humorous": "negative", "humorous": "excluded"},
        "negatives_basis": {
            **HUMAN_BASIS,
            "label_column": "label",
            "negative_values": ["not_humorous"],
        },
    }
    response = await client.post(f"{API}/detector-sets", json=body)
    assert response.status_code == 422, response.text
    assert "labeler" in response.json()["error"]["message"]


def test_the_database_refuses_an_unknown_or_incomplete_basis(clean_db: None) -> None:
    import pytest
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    from tests.support import db_factories

    with sync_session_factory()() as db:
        v = db_factories.version(db)
        db.execute(
            text(
                "INSERT INTO dw_detector_sets (id, name, description, positive_meaning, archived, "
                "created_by, created_by_origin) VALUES ('dts_basis', 'basis', '', '', false, "
                "'T', 'operator')"
            )
        )
        db.commit()
        insert = text(
            "INSERT INTO dw_detector_set_roles (id, set_id, role, position, version_id, split, "
            "input_column, label_column, label_mapping, negatives_basis, display_name) VALUES "
            "(:id, 'dts_basis', 'calibration_negatives', 0, :v, 'train', 'text', 'label', "
            "'{}'::jsonb, CAST(:basis AS jsonb), '')"
        )
        bad = [
            '{"kind": "made_up"}',
            "{}",  # no kind at all: ->>'kind' is SQL NULL, and a NULL CHECK passes (C25)
            '{"rule": "people said so"}',
            '{"kind": "human_labelled", "negative_values": ["0"]}',
            '{"kind": "human_labelled", "label_column": "label"}',
            '{"kind": "human_labelled", "label_column": "label", "negative_values": "0"}',
            "null",
        ]
        for n, basis in enumerate(bad):
            with pytest.raises(IntegrityError, match="calibration_has_basis"):
                db.execute(insert, {"id": f"dsr_bad{n}", "v": v.id, "basis": basis})
                db.commit()
            db.rollback()
        good = '{"kind": "human_labelled", "label_column": "label", "negative_values": ["0"]}'
        db.execute(insert, {"id": "dsr_good", "v": v.id, "basis": good})
        db.commit()


# --- defect 3: null labels are a value -----------------------------------------------------------


async def test_null_labels_are_counted_mapped_and_expected(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    created = await create(client, humicroedit_body(humicroedit_version(), label_sources=SOURCES))
    checks = await checks_of(client, created["id"])
    assert outcome(checks, "D-2")["outcome"] == "green", outcome(checks, "D-2")
    for kind, split in (
        ("train", "train"),
        ("id_test", "test"),
        ("calibration_negatives", "validation"),
    ):
        originals, funny, unfunny, middle = HUMICROEDIT_SHAPE[split]
        rid = role_of(created, kind)["id"]
        # every row counted, nulls under "None", as miStudio names them
        assert checks["label_values"][rid] == {
            "0": originals + unfunny,
            "1": funny,
            "None": middle,
        }, kind
        expected = checks["expected_counts"][rid]
        if kind == "calibration_negatives":
            assert expected == {
                "positive": 0,
                "negative": originals + unfunny,
                "excluded": funny + middle,
            }
        else:
            assert expected == {
                "positive": funny,
                "negative": originals + unfunny,
                "excluded": middle,
            }, kind


async def test_d2_refuses_a_mapping_that_forgets_null(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    v = humicroedit_version()
    body = humicroedit_body(v)
    for r in body["roles"]:
        r["label_mapping"] = {k: t for k, t in r["label_mapping"].items() if k != "None"}
    created = await create(client, body)
    d2 = outcome(await checks_of(client, created["id"]), "D-2")
    assert d2["outcome"] == "refused", d2
    unmapped = [p for p in d2["details"]["problems"] if p["code"] == "unmapped_value"]
    assert {p["value"] for p in unmapped} == {"None"}
    assert len(unmapped) == 3  # train, in-distribution test AND calibration negatives
    assert "Null label values" in d2["reason"]
    # miStudio also reads the key "null" for a null label
    body = humicroedit_body(v, name="hum-null-key")
    for r in body["roles"]:
        r["label_mapping"] = {
            ("null" if k == "None" else k): t for k, t in r["label_mapping"].items()
        }
    created = await create(client, body)
    checks = await checks_of(client, created["id"])
    assert outcome(checks, "D-2")["outcome"] == "green"
    train = role_of(created, "train")["id"]
    assert checks["expected_counts"][train]["excluded"] == HUMICROEDIT_SHAPE["train"][3]
