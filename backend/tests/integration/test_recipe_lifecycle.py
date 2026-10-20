"""Recipes: validate, save, revise, clone, archive, export, import (tasks 4.2–4.6).

Feature 003's registry is the stub registry (FR-003.3), installed per test.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from src.core.canonical_json import canonical_json
from src.core.database import get_sync_engine
from tests.support.stub_operators import StubRegistry, body, install_stubs

R = "/api/v1/recipes"


@pytest.fixture
def stubs(monkeypatch: pytest.MonkeyPatch) -> StubRegistry:
    return install_stubs(monkeypatch)


def recipe(min_len: int = 8) -> dict[str, Any]:
    return body(("stub_drop_short", {"min_len": min_len}), ("stub_keep", {}))


async def _create(
    client: httpx.AsyncClient, name: str = "humor-curation", **extra: Any
) -> dict[str, Any]:
    payload = {"name": name, "body": recipe(), **extra}
    response = await client.post(R, json=payload)
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()
    return data


class TestValidation:
    async def test_every_failing_step_is_reported_together(
        self, client: httpx.AsyncClient, stubs: StubRegistry
    ) -> None:
        stubs.not_allowed.add("stub_keep")
        bad = {
            "format": "dw.recipe/v1",
            "steps": [
                {"operator": "no_such_operator", "version": "1", "params": {}},
                {"operator": "stub_keep", "version": "1", "params": {}},
                {"operator": "stub_drop_short", "version": "1", "params": {"min_len": "abc"}},
                {"operator": "stub_drop_short", "version": "9", "params": {"min_len": 3}},
            ],
        }
        result = (await client.post(f"{R}/validate", json={"body": bad})).json()
        assert result["valid"] is False
        codes = {s["index"]: [e["code"] for e in s["errors"]] for s in result["steps"]}
        assert codes == {
            1: ["operator_not_found"],
            2: ["operator_not_allowed"],
            3: ["params_invalid"],
            4: ["version_unavailable"],
        }
        message = result["steps"][3]["errors"][0]["message"]
        assert "Clone recipe with current operators" in message and "stub_drop_short 1" in message

    async def test_a_missing_version_is_never_silently_upgraded(
        self, client: httpx.AsyncClient, stubs: StubRegistry, operator_name: str
    ) -> None:
        old = {
            "format": "dw.recipe/v1",
            "steps": [{"operator": "stub_keep", "version": "0.9", "params": {}}],
        }
        response = await client.post(R, json={"name": "old", "body": old})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "recipe_invalid"
        assert response.json()["error"]["details"]["steps"][0]["version"] == "0.9"

    async def test_unknown_body_fields_are_refused_not_dropped(
        self, client: httpx.AsyncClient, stubs: StubRegistry
    ) -> None:
        extra = recipe() | {"comment": "x"}
        result = (await client.post(f"{R}/validate", json={"body": extra})).json()
        assert result["valid"] is False and result["body_errors"][0]["code"] == "body_invalid"
        step_extra = body(("stub_keep", {}))
        step_extra["steps"][0]["label"] = "keep everything"
        result = (await client.post(f"{R}/validate", json={"body": step_extra})).json()
        assert result["valid"] is False

    async def test_missing_input_columns_are_found_when_columns_are_known(
        self, stubs: StubRegistry
    ) -> None:
        from src.services.recipe_service import validate_body

        result, _ = validate_body(body(("stub_band", {"min": 0.3, "max": 0.7})), {"text"})
        assert [e.code for e in result.steps[0].errors] == ["missing_input_column"]
        ok, _ = validate_body(body(("stub_band", {"min": 0.3, "max": 0.7})), {"text", "score"})
        assert ok.valid

    async def test_production_with_no_registry_refuses_every_operator(
        self, client: httpx.AsyncClient
    ) -> None:
        """Without feature 003 nothing is runnable — said plainly, never guessed."""
        result = (await client.post(f"{R}/validate", json={"body": recipe()})).json()
        assert result["valid"] is False
        assert "feature 003" in result["steps"][0]["errors"][0]["message"]


class TestSaveReviseCloneArchive:
    async def test_save_records_the_canonical_hash(
        self, client: httpx.AsyncClient, stubs: StubRegistry, operator_name: str
    ) -> None:
        created = await _create(
            client, description="drop short jokes", step_labels=["short", "all"]
        )
        head = created["revisions"][0]
        expected = hashlib.sha256(canonical_json(recipe())).hexdigest()
        assert head["recipe_hash"] == expected == created["head_hash"]
        assert created["step_count"] == 2 and created["providers"] == ["native"]
        assert head["created_by"] == operator_name and head["step_labels"] == ["short", "all"]

    async def test_name_description_and_labels_stay_out_of_the_hash(
        self, client: httpx.AsyncClient, stubs: StubRegistry, operator_name: str
    ) -> None:
        a = await _create(client, "a", description="one", step_labels=["x", "y"])
        b = await _create(client, "b", description="two", step_labels=["p", "q"])
        assert a["head_hash"] == b["head_hash"]
        with get_sync_engine().connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM dw_recipe_bodies")).scalar_one() == 1

    async def test_a_taken_name_is_refused(
        self, client: httpx.AsyncClient, stubs: StubRegistry, operator_name: str
    ) -> None:
        await _create(client)
        response = await client.post(R, json={"name": "humor-curation", "body": recipe()})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "recipe_name_taken"

    async def test_revise_moves_the_head_and_keeps_the_old_revision(
        self, client: httpx.AsyncClient, stubs: StubRegistry, operator_name: str
    ) -> None:
        created = await _create(client)
        revised = await client.post(f"{R}/{created['id']}/revisions", json={"body": recipe(12)})
        assert revised.status_code == 201
        assert revised.json()["revision_number"] == 2
        got = (await client.get(f"{R}/{created['id']}")).json()
        assert got["head_revision_id"] == revised.json()["id"]
        assert [r["revision_number"] for r in got["revisions"]] == [1, 2]
        assert got["revisions"][0]["recipe_hash"] != got["revisions"][1]["recipe_hash"]

    async def test_an_invalid_revision_is_refused(
        self, client: httpx.AsyncClient, stubs: StubRegistry, operator_name: str
    ) -> None:
        created = await _create(client)
        response = await client.post(f"{R}/{created['id']}/revisions", json={"body": recipe(-1)})
        assert response.status_code == 422 and response.json()["error"]["code"] == "recipe_invalid"

    async def test_clone_records_its_source(
        self, client: httpx.AsyncClient, stubs: StubRegistry, operator_name: str
    ) -> None:
        created = await _create(client)
        cloned = await client.post(f"{R}/{created['id']}/clone", json={"name": "copy"})
        assert cloned.status_code == 201
        revision = cloned.json()["revisions"][0]
        assert revision["cloned_from_revision_id"] == created["head_revision_id"]
        assert revision["recipe_hash"] == created["head_hash"]
        again = await client.post(f"{R}/{created['id']}/clone", json={"name": "copy"})
        assert again.json()["error"]["code"] == "recipe_name_taken"

    async def test_archive_hides_refuses_revisions_and_keeps_the_body(
        self, client: httpx.AsyncClient, stubs: StubRegistry, operator_name: str
    ) -> None:
        created = await _create(client)
        archived = await client.post(f"{R}/{created['id']}/archive")
        assert archived.status_code == 200 and archived.json()["archived"] is True
        assert archived.json()["archived_by"] == operator_name
        assert (await client.get(R)).json()["total"] == 0
        assert (await client.get(f"{R}?archived=true")).json()["total"] == 1
        response = await client.post(f"{R}/{created['id']}/revisions", json={"body": recipe(3)})
        assert response.status_code == 409 and response.json()["error"]["code"] == "recipe_archived"
        still = (await client.get(f"{R}/{created['id']}")).json()
        assert still["revisions"][0]["body"] == recipe()

    async def test_unknown_recipe_is_404(self, client: httpx.AsyncClient) -> None:
        response = await client.get(f"{R}/00000000-0000-0000-0000-000000000000")
        assert (
            response.status_code == 404 and response.json()["error"]["code"] == "recipe_not_found"
        )


class TestExportImport:
    async def test_export_is_canonical_and_its_body_hashes_to_the_recipe_hash(
        self, client: httpx.AsyncClient, stubs: StubRegistry, operator_name: str
    ) -> None:
        created = await _create(client)
        url = f"{R}/{created['id']}/revisions/{created['head_revision_id']}/export"
        first = await client.get(url)
        second = await client.get(url)
        assert first.status_code == 200 and first.content == second.content
        document = json.loads(first.content)
        assert canonical_json(document) == first.content
        assert hashlib.sha256(canonical_json(document["body"])).hexdigest() == created["head_hash"]
        assert document["recipe_hash"] == created["head_hash"]
        assert "humor-curation.recipe.json" in first.headers["content-disposition"]

    async def test_export_then_import_into_a_clean_database_keeps_the_hash(
        self, client: httpx.AsyncClient, stubs: StubRegistry, operator_name: str, clean_db: None
    ) -> None:
        created = await _create(client)
        exported = (
            await client.get(f"{R}/{created['id']}/revisions/{created['head_revision_id']}/export")
        ).content
        with get_sync_engine().begin() as conn:
            conn.execute(
                text(
                    "TRUNCATE dw_recipe_revisions, dw_recipes, dw_recipe_bodies RESTART IDENTITY "
                    "CASCADE"
                )
            )
        imported = await client.post(f"{R}/import", files={"file": ("r.json", exported)})
        result = imported.json()
        assert result["outcome"] == "created", result
        assert result["hash"] == created["head_hash"]
        assert result["recipe"]["revisions"][0]["imported"] is True
        assert result["notes"] == []
        again = (await client.post(f"{R}/import", files={"file": ("r.json", exported)})).json()
        assert again["outcome"] == "already_present"

    async def test_a_hand_edited_file_is_refused_with_hash_mismatch(
        self, client: httpx.AsyncClient, stubs: StubRegistry, operator_name: str
    ) -> None:
        created = await _create(client)
        document = json.loads(
            (
                await client.get(
                    f"{R}/{created['id']}/revisions/{created['head_revision_id']}/export"
                )
            ).content
        )
        document["body"]["steps"][0]["params"]["min_len"] = 99
        result = (
            await client.post(f"{R}/import", files={"file": ("r.json", canonical_json(document))})
        ).json()
        assert result["outcome"] == "refused"
        assert [r["code"] for r in result["reasons"]] == ["hash_mismatch"]

    async def test_a_reindented_file_imports_with_a_note(
        self, client: httpx.AsyncClient, stubs: StubRegistry, operator_name: str
    ) -> None:
        created = await _create(client)
        document = json.loads(
            (
                await client.get(
                    f"{R}/{created['id']}/revisions/{created['head_revision_id']}/export"
                )
            ).content
        )
        document["name"] = "pretty"
        pretty = json.dumps(document, indent=2).encode()
        result = (await client.post(f"{R}/import", files={"file": ("r.json", pretty)})).json()
        assert result["outcome"] == "created" and result["hash"] == created["head_hash"]
        assert result["notes"] and result["notes"][0].startswith("file_not_canonical")

    async def test_an_invalid_recipe_file_is_refused_with_reasons(
        self, client: httpx.AsyncClient, stubs: StubRegistry, operator_name: str
    ) -> None:
        bad_body = body(("stub_drop_short", {"min_len": "x"}))
        document = {
            "format": "dw.recipe-file/v1",
            "name": "bad",
            "description": None,
            "step_labels": [],
            "recipe_hash": hashlib.sha256(canonical_json(bad_body)).hexdigest(),
            "body": bad_body,
        }
        result = (
            await client.post(f"{R}/import", files={"file": ("r.json", canonical_json(document))})
        ).json()
        assert result["outcome"] == "refused"
        assert result["reasons"][0]["code"] == "params_invalid"

    @pytest.mark.parametrize(
        ("content", "status"),
        [(b"not json", 422), (b'{"format": "other"}', 422), (b"x" * 1_000_001, 413)],
        ids=["not-json", "wrong-format", "over-1MB"],
    )
    async def test_import_refusals(
        self,
        client: httpx.AsyncClient,
        stubs: StubRegistry,
        operator_name: str,
        content: bytes,
        status: int,
    ) -> None:
        response = await client.post(f"{R}/import", files={"file": ("r.json", content)})
        assert response.status_code == status
        assert response.json()["error"]["code"] == "recipe_import_invalid"


async def test_actions_need_an_operator_name(
    client: httpx.AsyncClient, stubs: StubRegistry
) -> None:
    response = await client.post(R, json={"name": "x", "body": recipe()})
    assert response.status_code == 422 and response.json()["error"]["code"] == "NO_IDENTITY"


async def test_an_agent_is_recorded_as_who(client: httpx.AsyncClient, stubs: StubRegistry) -> None:
    response = await client.post(
        R, json={"name": "agent-made", "body": recipe()}, headers={"X-Dataworks-Agent": "agent:mcp"}
    )
    assert response.status_code == 201
    assert response.json()["revisions"][0]["created_by_origin"] == "agent"
    assert response.json()["created_by"] == "agent:mcp"
