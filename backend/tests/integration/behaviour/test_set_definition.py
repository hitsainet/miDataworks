"""A detector set records the behaviour definition it is made against (017 FTASKS 3.6; FR-017.11).

Supplied as ``dataworks_resolve_behaviour``'s ``record`` (resolved here from the fake catalogue at
v1), or taken from the training version's recorded definition; a definition naming another key
than the set's is refused. Read back through REST and through the MCP tool over the same backend.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
import pytest
from sqlalchemy import text

from src.clients import mistudio_client as mc
from src.core.config import get_settings
from src.core.database import sync_session_factory
from tests.support.detector_fixtures import humor_versions, set_body
from tests.support.fake_mistudio import BASE, FakeMiStudio, catalogue_head
from tests.support.mcp_live import call, live_mcp

S = "/api/v1/detector-sets"


@pytest.fixture
def catalogue(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeMiStudio]:
    fake = FakeMiStudio()
    monkeypatch.setattr(mc, "TRANSPORT", fake.transport())
    monkeypatch.setattr(get_settings(), "mistudio_base_url", BASE)
    yield fake


def record_on_version(version_id: str, recorded: dict) -> None:
    """As finalize writes it (the set's derivation is under test here, not the build)."""
    with sync_session_factory()() as db:
        db.execute(text("ALTER TABLE dw_versions DISABLE TRIGGER dw_versions_immutable"))
        db.execute(
            text("UPDATE dw_versions SET behaviour_definition = CAST(:d AS jsonb) WHERE id = :v"),
            {"d": json.dumps(recorded), "v": version_id},
        )
        db.execute(text("ALTER TABLE dw_versions ENABLE TRIGGER dw_versions_immutable"))
        db.commit()


async def test_a_set_records_the_resolved_definition_and_reads_it_back(
    client: httpx.AsyncClient, operator_name: str, catalogue: FakeMiStudio
) -> None:
    catalogue.behaviours["key-one"] = catalogue_head("key-one", version=1, positive_meaning="m1")
    resolved = (await client.get("/api/v1/behaviours/key-one/resolve")).json()
    versions = humor_versions()
    created = await client.post(
        S,
        json=set_body(
            versions,
            name="defined",
            behaviour_key="key-one",
            behaviour_definition=resolved["record"],
        ),
    )
    assert created.status_code == 201, created.text
    recorded = created.json()["behaviour_definition"]
    assert recorded["definition_version"] == 1 and recorded["verified"] is True
    assert recorded["key"] == "key-one" and recorded["positive_meaning"] == "m1"
    mcp, mcp_client = live_mcp()
    try:
        via = await call(mcp, "dataworks_get_detector_set", {"set_id": created.json()["id"]})
    finally:
        await mcp_client.close()
    assert via["behaviour_definition"] == recorded
    # the catalogue moves on; the set keeps what it was made against (nothing rebuilds by itself)
    catalogue.behaviours["key-one"] = catalogue_head("key-one", version=2)
    again = (await client.get(f"{S}/{created.json()['id']}")).json()
    assert again["behaviour_definition"]["definition_version"] == 1


async def test_without_one_the_training_versions_definition_is_taken(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    versions = humor_versions()
    built_against = {
        "key": "key-two",
        "definition_version": 4,
        "positive_meaning": "it shows",
        "verified": True,
        "source": "catalogue",
        "read_at": "2026-10-24T00:00:00+00:00",
    }
    record_on_version(versions["train"], built_against)
    created = await client.post(S, json=set_body(versions, name="derived", behaviour_key="key-two"))
    assert created.status_code == 201, created.text
    assert created.json()["behaviour_definition"] == built_against
    other = await client.post(S, json=set_body(versions, name="other", behaviour_key="key-three"))
    assert other.json()["behaviour_definition"] is None, "another key's definition is not taken"
    refused = await client.post(
        S,
        json=set_body(
            versions,
            name="mismatch",
            behaviour_key="key-three",
            behaviour_definition=built_against,
        ),
    )
    assert refused.status_code == 422, refused.text
    error = refused.json()["error"]
    assert error["code"] == "behaviour_definition_mismatch"
    assert error["details"]["next"]["tool"] == "dataworks_resolve_behaviour"
    # an update re-keying the set to the version's behaviour takes its definition then
    moved = await client.patch(f"{S}/{other.json()['id']}", json={"behaviour_key": "key-two"})
    assert moved.status_code == 200 and moved.json()["behaviour_definition"] == built_against
    # Review R2: re-keying a set AWAY from its recorded definition drops it (re-derived from the
    # training version, which names the old key): a set never keeps another behaviour's definition
    away = await client.patch(f"{S}/{created.json()['id']}", json={"behaviour_key": "key-three"})
    assert away.status_code == 200, away.text
    assert away.json()["behaviour_key"] == "key-three"
    assert away.json()["behaviour_definition"] is None
