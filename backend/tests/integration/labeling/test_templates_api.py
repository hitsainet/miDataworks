"""Decision templates and rubrics over REST (005 FTASKS 4.x; FR-005.11 – FR-005.17)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.core.database import async_session_factory, sync_session_factory
from src.schemas.labeling import TEIClassificationTemplate
from src.services import decision_template_service as svc
from tests.integration.labeling.helpers import setup_classifier, start_body
from tests.support.labeling_fixtures import Labeling
from tests.support.source_tree import source_only

TEI_BODY = {
    "kind": "tei_classification",
    "render": "{text}",
    "input_fields": ["text"],
    "label_set": ["benign", "injection"],
    "positive_class": "injection",
    "label_map": {"SAFE": "benign", "INJECTION": "injection"},
}

RUBRIC = {
    "style": "pointwise",
    "messages": [{"role": "user", "content": "Is {text} funny? End with VERDICT: yes or no."}],
    "input_fields": ["text"],
    "parser": "verdict_line_v1",
    "allowed_verdicts": ["yes", "no"],
}


def test_the_builtin_jev_template_equals_the_prototype_record() -> None:
    record = json.loads(source_only("records/jev_decision_client.json").read_text())
    (doc,) = svc.builtin_template_documents()
    body = doc.body.model_dump()
    assert body["verbalizer_ids"] == record["verbalizer_ids"]
    assert body["verbalizer_ids"][:2] == [3721, 1802]
    assert {k: list(v) for k, v in body["slots"].items()} == record["slots"]
    assert body["bias"] == record["bias"]
    assert body["temperature"] == record["temperature"]
    assert (
        body["bound_model_revision"]
        == record["revision"]
        == "b63f651ce8ed64481d3f5e73ecdb05f740042f01"
    )
    assert body["bound_model_id"] == "JEV-9B-decision"
    assert body["tokenization"] == {"add_special_tokens": False}
    assert record["template"] == "bare-v1"


async def test_list_seeds_the_builtin_once(client: httpx.AsyncClient) -> None:
    for _ in range(2):
        items = (await client.get("/api/v1/decision-templates")).json()
    assert [t["ref"] for t in items] == ["jev/noul-bare-v1@1"]
    assert items[0]["created_by_origin"] == "system"


async def test_create_clone_export_import_round_trip(client: httpx.AsyncClient) -> None:
    from tests.support.labeling_fixtures import set_operator

    set_operator()
    created = await client.post(
        "/api/v1/decision-templates", json={"name": "deberta/x", "body": TEI_BODY}
    )
    assert created.status_code == 201, created.text
    first = created.json()
    assert first["version"] == 1 and first["created_by"] == "Test Operator"
    clone = (await client.post(f"/api/v1/decision-templates/{first['id']}/clone", json={})).json()
    assert clone["version"] == 2 and clone["content_hash"] != first["content_hash"]
    exported = (await client.get(f"/api/v1/decision-templates/{first['id']}/export")).json()
    assert exported["format"] == "midataworks.decision-template/v1"
    again = (await client.post("/api/v1/decision-templates/import", json=exported)).json()
    assert again["id"] == first["id"] and again["content_hash"] == first["content_hash"]
    exported["body"]["render"] = "{text}!"
    clash = await client.post("/api/v1/decision-templates/import", json=exported)
    assert clash.status_code == 409 and clash.json()["error"]["code"] == "TEMPLATE_VERSION_EXISTS"


async def test_import_refuses_an_unknown_key(client: httpx.AsyncClient) -> None:
    doc = {
        "format": "midataworks.decision-template/v1",
        "name": "x",
        "version": 1,
        "body": {**TEI_BODY, "surprise": 1},
    }
    response = await client.post("/api/v1/decision-templates/import", json=doc)
    assert response.status_code == 422


async def test_a_used_template_cannot_change_or_be_deleted(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    version_id, template_id = await setup_classifier(client, labeling, n=3)
    assert (
        await client.post("/api/v1/label-runs", json=start_body(version_id, template_id))
    ).status_code == 201
    body = TEIClassificationTemplate.model_validate(TEI_BODY)
    async with async_session_factory()() as db:
        with pytest.raises(svc.ConflictError) as exc:
            await svc.update_template(db, template_id, body)  # type: ignore[arg-type]
        assert exc.value.code == "TEMPLATE_IMMUTABLE"
        with pytest.raises(svc.ConflictError):
            await svc.delete_template(db, template_id)
    with sync_session_factory()() as db, pytest.raises(IntegrityError):
        db.execute(text("DELETE FROM dw_decision_templates WHERE id = :i"), {"i": template_id})
        db.commit()


async def test_an_unused_template_can_be_updated(client: httpx.AsyncClient) -> None:
    from tests.support.labeling_fixtures import set_operator

    set_operator()
    first = (
        await client.post(
            "/api/v1/decision-templates", json={"name": "deberta/y", "body": TEI_BODY}
        )
    ).json()
    body = TEIClassificationTemplate.model_validate({**TEI_BODY, "render": "Q: {text}"})
    async with async_session_factory()() as db:
        row = await svc.update_template(db, first["id"], body)  # type: ignore[arg-type]
        assert row.content_hash != first["content_hash"]


async def test_token_ids_without_a_bound_model_are_refused_by_the_table() -> None:
    with sync_session_factory()() as db, pytest.raises(IntegrityError):
        db.execute(
            text(
                "INSERT INTO dw_decision_templates (id, name, version, content_hash, body, protocol, created_by, created_by_origin) "
                "VALUES ('dt_x', 'n', 1, :h, '{\"verbalizer_ids\": [1]}', 'openai_scoring', 'me', 'operator')"
            ),
            {"h": "a" * 64},
        )
        db.commit()


async def test_rubrics_round_trip(client: httpx.AsyncClient) -> None:
    from tests.support.labeling_fixtures import set_operator

    set_operator()
    created = (
        await client.post("/api/v1/rubrics", json={"name": "humor/judge", "body": RUBRIC})
    ).json()
    assert created["style"] == "pointwise" and created["version"] == 1
    exported = (await client.get(f"/api/v1/rubrics/{created['id']}/export")).json()
    again = (await client.post("/api/v1/rubrics/import", json=exported)).json()
    assert again["id"] == created["id"]
    clone = (await client.post(f"/api/v1/rubrics/{created['id']}/clone", json={})).json()
    assert clone["version"] == 2
    bad: dict[str, Any] = {**RUBRIC, "parser": "json_v1"}  # json_v1 needs a schema
    assert (
        await client.post("/api/v1/rubrics", json={"name": "r", "body": bad})
    ).status_code == 422
