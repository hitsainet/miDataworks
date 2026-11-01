"""Resolve a behaviour key (017 FR-017.10; FTASKS 3.5).

Through the real route with the fake miStudio's catalogue (payload and call count asserted), and
standalone: no miStudio, a ``404``, a ``5xx`` and a dropped connection each answer "not verified"
or "not in the catalogue", never "verified". The MCP tool's call is pinned by the ledger
(``dataworks_resolve_behaviour``), the route by ``tests/unit/test_reachability.py``.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest

from src.clients import mistudio_client as mc
from src.core.config import get_settings
from src.schemas.behaviour import BEHAVIOUR_KEY_PATTERN
from tests.support.fake_mistudio import BASE, FakeMiStudio, catalogue_head

R = "/api/v1/behaviours"
PATH = "/api/v1/probe-monitors/behaviours/"


@pytest.fixture
def catalogue(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeMiStudio]:
    fake = FakeMiStudio()
    monkeypatch.setattr(mc, "TRANSPORT", fake.transport())
    monkeypatch.setattr(get_settings(), "mistudio_base_url", BASE)
    yield fake


async def resolve(client: httpx.AsyncClient, key: str) -> dict:
    response = await client.get(f"{R}/{key}/resolve")
    assert response.status_code == 200, response.text
    return dict(response.json())


async def test_the_answer_is_the_catalogues_now(
    client: httpx.AsyncClient, catalogue: FakeMiStudio
) -> None:
    catalogue.behaviours["key-one"] = catalogue_head(
        "key-one", version=2, positive_meaning="the reply shows it", target_fpr=0.02
    )
    first = await resolve(client, "key-one")
    assert first["verified"] is True and first["reason"] == "verified against miStudio's catalogue"
    assert first["definition"] == {
        "version": 2,
        "positive_meaning": "the reply shows it",
        "negative_meaning": "the text does not",
        "target_fpr": 0.02,
    }
    assert first["current_version"] == 2 and first["archived"] is False
    record = first["record"]
    assert record["key"] == "key-one" and record["definition_version"] == 2
    assert record["verified"] is True and record["source"] == "catalogue"
    assert record["positive_meaning"] == "the reply shows it"
    # no cache: changing the catalogue changes the next answer
    catalogue.behaviours["key-one"] = catalogue_head(
        "key-one", version=3, positive_meaning="moved", archived=True
    )
    second = await resolve(client, "key-one")
    assert second["definition"]["version"] == 3 and second["record"]["definition_version"] == 3
    assert second["archived"] is True and second["definition"]["positive_meaning"] == "moved"
    calls = catalogue.calls("GET", PATH)
    assert [c.path for c in calls] == [PATH + "key-one", PATH + "key-one"]
    assert all(c.body is None and "authorization" not in c.headers for c in calls)


async def test_a_key_not_in_the_catalogue(
    client: httpx.AsyncClient, catalogue: FakeMiStudio
) -> None:
    answer = await resolve(client, "key-absent")
    assert answer["verified"] is False and answer["reason"] == "not in the catalogue"
    assert answer["definition"] is None and answer["record"]["source"] == "supplied"
    assert answer["record"]["verified"] is False and answer["record"]["key"] == "key-absent"
    assert catalogue.count("GET", PATH) == 1


@pytest.mark.parametrize(
    "failure",
    [
        {"status": 503, "body": {"detail": "down"}},
        {"status": 500, "body": {"detail": "boom"}},
        # Review R2: a 404 that is not the catalogue's own ``not_found`` (a miStudio without 037's
        # route, or a proxy) is not "not in the catalogue"
        {"status": 404, "body": {"detail": "Not Found"}},
        {"drop": True},
        {"non_json": True},
    ],
)
async def test_a_catalogue_that_does_not_answer_is_not_verified(
    client: httpx.AsyncClient, catalogue: FakeMiStudio, failure: dict
) -> None:
    catalogue.behaviours["key-one"] = catalogue_head("key-one")
    catalogue.fail("GET /probe-monitors/behaviours/{key}", **failure)
    answer = await resolve(client, "key-one")
    assert answer["verified"] is False
    assert answer["reason"] == "not verified against a catalogue", answer
    assert answer["definition"] is None and answer["record"]["source"] == "supplied"
    assert catalogue.count("GET", PATH) == 1


async def test_a_head_for_another_key_is_not_verified(
    client: httpx.AsyncClient, catalogue: FakeMiStudio
) -> None:
    """Review R2: the catalogue answering with ANOTHER key's head (a redirect, a stale alias) is
    never recorded as this key's verified definition."""
    catalogue.behaviours["key-one"] = catalogue_head("key-two", version=4)
    answer = await resolve(client, "key-one")
    assert answer["verified"] is False
    assert answer["reason"] == "not verified against a catalogue", answer
    assert answer["record"]["key"] == "key-one" and answer["record"]["source"] == "supplied"
    assert answer["record"]["definition_version"] is None
    assert catalogue.count("GET", PATH) == 1


async def test_standalone_without_mistudio(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "mistudio_base_url", None)
    answer = await resolve(client, "key-one")
    assert answer["verified"] is False
    assert answer["reason"] == "not verified against a catalogue"
    assert "MISTUDIO_BASE_URL" in answer["detail"]


async def test_a_key_breaking_the_rule_is_refused(client: httpx.AsyncClient) -> None:
    response = await client.get(f"{R}/Not_A_Key/resolve")
    assert response.status_code == 422
    assert BEHAVIOUR_KEY_PATTERN in response.text
