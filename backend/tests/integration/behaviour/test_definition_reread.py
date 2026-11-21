"""ENH-006 G20: a new set takes the catalogue's CURRENT definition; a re-read moves a set on request.

The gap: after miWorkbench recorded ``dlp-ssn-us`` version 2 and ``dataworks_resolve_behaviour`` read
it, three new sets still held the version-1 snapshot (taken from their training version), and a new
set's positive meaning came back empty until set by hand. Operator decision 2026-11-08: a new set
defaults to the catalogue's current definition; existing sets and versions keep theirs; a "re-read
definition" action updates a set on request. Standalone-first: with miWorkbench not configured (or the
key not catalogued) there is no current definition to take, and the training version's is taken
as before, SAID in the answer; a configured catalogue that cannot be read is refused with its
reason, never replaced by the older snapshot.

Driven through REST and through the PRODUCTION MCP registry (``build_server``), over a fake miWorkbench
catalogue.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from src.clients import miworkbench_client as mc
from src.core.config import get_settings
from src.core.database import sync_session_factory
from src.models.detector_set import DetectorSet
from src.models.version import Version
from tests.integration.behaviour.test_set_definition import record_on_version
from tests.support.detector_fixtures import humor_versions, set_body
from tests.support.fake_miworkbench import BASE, FakeMiWorkbench, catalogue_head
from tests.support.mcp_live import call, production_mcp, refusal_of

S = "/api/v1/detector-sets"
KEY = "dlp-ssn-us"
ROUTE = "GET /probe-monitors/behaviours/{key}"
V1_MEANING = "the text holds a US social security number"
V2_MEANING = "the text discloses a US social security number of a real person"
BUILT_V1 = {
    "key": KEY,
    "definition_version": 1,
    "positive_meaning": V1_MEANING,
    "verified": True,
    "source": "catalogue",
    "read_at": "2026-10-30T00:00:00+00:00",
}


@pytest.fixture
def catalogue(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeMiWorkbench]:
    fake = FakeMiWorkbench()
    monkeypatch.setattr(mc, "TRANSPORT", fake.transport())
    monkeypatch.setattr(get_settings(), "mistudio_base_url", BASE)
    yield fake


def built_against_v1() -> dict[str, str]:
    """Versions whose training version was built against v1 (as finalize records it)."""
    versions = humor_versions()
    record_on_version(versions["train"], BUILT_V1)
    return versions


def sets_named(name: str) -> int:
    with sync_session_factory()() as db:
        return len(db.query(DetectorSet).filter(DetectorSet.name == name).all())


def d10(result: dict[str, Any]) -> dict[str, Any]:
    found: dict[str, Any] = next(o for o in result["outcomes"] if o["code"] == "D-10")
    return found


async def test_a_new_set_takes_the_catalogues_current_definition_and_meaning(
    client: httpx.AsyncClient, operator_name: str, catalogue: FakeMiWorkbench
) -> None:
    catalogue.behaviours[KEY] = catalogue_head(KEY, version=2, positive_meaning=V2_MEANING)
    versions = built_against_v1()
    created = await client.post(
        S, json=set_body(versions, name="new-set", behaviour_key=KEY, positive_meaning="")
    )
    assert created.status_code == 201, created.text
    out = created.json()
    assert out["definition_from"] == "catalogue"
    assert out["behaviour_definition"]["definition_version"] == 2, "the v1 snapshot was taken"
    assert out["behaviour_definition"]["source"] == "catalogue"
    assert out["behaviour_definition"]["verified"] is True
    assert out["positive_meaning"] == V2_MEANING, "a new set's meaning came back empty"
    # A meaning the request gives is the operator's: kept (D-10 names any mismatch).
    given = await client.post(S, json=set_body(versions, name="given-meaning", behaviour_key=KEY))
    assert given.status_code == 201, given.text
    assert given.json()["behaviour_definition"]["definition_version"] == 2
    assert given.json()["positive_meaning"] == "Most readers would find the text funny"
    # The training version keeps what it was built against (immutable).
    with sync_session_factory()() as db:
        train = db.get(Version, versions["train"])
        assert train is not None and train.behaviour_definition == BUILT_V1


async def test_an_existing_set_keeps_its_definition_until_it_is_re_read(
    client: httpx.AsyncClient,
    operator_name: str,
    catalogue: FakeMiWorkbench,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalogue.behaviours[KEY] = catalogue_head(KEY, version=2, positive_meaning=V2_MEANING)
    versions = built_against_v1()
    created = await client.post(
        S, json=set_body(versions, name="kept", behaviour_key=KEY, positive_meaning="")
    )
    set_id = created.json()["id"]
    catalogue.behaviours[KEY] = catalogue_head(KEY, version=3, positive_meaning="v3 meaning")
    again = (await client.get(f"{S}/{set_id}")).json()
    assert again["behaviour_definition"]["definition_version"] == 2, "a set moved by itself"
    assert again["positive_meaning"] == V2_MEANING

    # D-10 names the moved definition AND the re-read as its next step.
    checked = (await client.post(f"{S}/{set_id}/checks")).json()
    outcome = d10(checked)
    assert "The set was made against v2, now v3." in outcome["details"]["notes"]
    assert outcome["details"]["next"] == {
        "tool": "dataworks_reread_behaviour_definition",
        "args": {"set_id": set_id},
        "why": "the set takes the catalogue's v3 definition and positive meaning; its versions "
        "keep what they were built against",
    }
    assert checked["send_allowed"] is True, "D-10 notes; it never refuses"

    # The re-read, through the production MCP registry.
    mcp, mcp_client = production_mcp(monkeypatch)
    try:
        before = catalogue.count("GET", "/api/v1/probe-monitors/behaviours/")
        answer = await call(mcp, "dataworks_reread_behaviour_definition", {"set_id": set_id})
        reads = catalogue.count("GET", "/api/v1/probe-monitors/behaviours/") - before
    finally:
        await mcp_client.close()
    assert reads == 1, "one catalogue read per re-read"
    definition = answer["behaviour_definition"]
    assert definition["definition_version"] == 3 and definition["positive_meaning"] == "v3 meaning"
    assert definition["reread"]["by"] == "agent:dataworks-mcp"
    assert definition["reread"]["origin"] == "agent"
    assert definition["reread"]["replaced"]["definition_version"] == 2
    assert definition["reread"]["replaced"]["positive_meaning"] == V2_MEANING
    assert definition["reread"]["set_positive_meaning_before"] == V2_MEANING
    assert answer["positive_meaning"] == "v3 meaning"
    assert answer["definition_reread"] == {
        "from_version": 2,
        "to_version": 3,
        "positive_meaning_changed": True,
        "versions_keep_theirs": True,
    }
    stored = (await client.get(f"{S}/{set_id}")).json()
    assert stored["behaviour_definition"] == definition, "the re-read was not recorded"
    with sync_session_factory()() as db:
        train = db.get(Version, versions["train"])
        assert train is not None and train.behaviour_definition == BUILT_V1

    # After the re-read the set matches the catalogue: no next step; the version still differs.
    outcome = d10((await client.post(f"{S}/{set_id}/checks")).json())
    assert "next" not in outcome["details"], outcome["details"].get("next")
    assert not any("The set was made against" in n for n in outcome["details"]["notes"])
    assert any("built against v1, now v3." in n for n in outcome["details"]["notes"])


async def test_an_unreadable_catalogue_is_refused_never_the_old_snapshot(
    client: httpx.AsyncClient, operator_name: str, catalogue: FakeMiWorkbench
) -> None:
    catalogue.behaviours[KEY] = catalogue_head(KEY, version=2, positive_meaning=V2_MEANING)
    versions = built_against_v1()
    catalogue.fail(ROUTE, drop=True)
    refused = await client.post(S, json=set_body(versions, name="unread", behaviour_key=KEY))
    assert refused.status_code == 502, refused.text
    error = refused.json()["error"]
    assert error["code"] == "behaviour_catalogue_unread"
    assert error["details"]["reason"] == "not verified against a catalogue"
    assert "miWorkbench's catalogue was not read" in error["details"]["detail"]
    assert error["details"]["next"]["tool"] == "dataworks_resolve_behaviour"
    assert sets_named("unread") == 0, "a refused create left a set"

    # The caller may choose a definition explicitly: that is never refused for the catalogue.
    chosen = await client.post(
        S,
        json=set_body(versions, name="chosen", behaviour_key=KEY, behaviour_definition=BUILT_V1),
    )
    assert chosen.status_code == 201, chosen.text
    assert chosen.json()["definition_from"] == "supplied"
    assert chosen.json()["behaviour_definition"] == BUILT_V1

    # A re-read that cannot read the catalogue changes nothing.
    set_id = chosen.json()["id"]
    catalogue.fail(ROUTE, status=500, body={"detail": "boom"})
    failed = await client.post(f"{S}/{set_id}/behaviour-definition/reread")
    assert failed.status_code == 502, failed.text
    assert failed.json()["error"]["code"] == "behaviour_catalogue_unread"
    assert (await client.get(f"{S}/{set_id}")).json()["behaviour_definition"] == BUILT_V1


async def test_standalone_takes_the_training_versions_definition_and_says_so(
    client: httpx.AsyncClient, operator_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """miDataworks without miWorkbench: no catalogue, so no current definition to take."""
    monkeypatch.setattr(get_settings(), "mistudio_base_url", None)
    versions = built_against_v1()
    created = await client.post(S, json=set_body(versions, name="standalone", behaviour_key=KEY))
    assert created.status_code == 201, created.text
    assert created.json()["definition_from"] == "training_version"
    assert created.json()["catalogue"] == "miWorkbench is not configured"
    assert created.json()["behaviour_definition"] == BUILT_V1
    refused = await client.post(f"{S}/{created.json()['id']}/behaviour-definition/reread")
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "mistudio_not_configured"
    assert refused.json()["error"]["details"]["next"]["tool"] == "dataworks_update_detector_set"


async def test_a_key_the_catalogue_does_not_hold_and_a_set_with_no_key(
    client: httpx.AsyncClient, operator_name: str, catalogue: FakeMiWorkbench
) -> None:
    versions = built_against_v1()
    created = await client.post(S, json=set_body(versions, name="uncatalogued", behaviour_key=KEY))
    assert created.status_code == 201, created.text
    assert created.json()["definition_from"] == "training_version"
    assert created.json()["catalogue"] == f"miWorkbench's catalogue has no behaviour {KEY!r}"
    refused = await client.post(f"{S}/{created.json()['id']}/behaviour-definition/reread")
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "behaviour_not_in_catalogue"
    keyless = await client.post(S, json=set_body(versions, name="keyless"))
    # A set with no key reads no catalogue (017: it takes its training version's record).
    assert keyless.json()["definition_from"] == "training_version", keyless.text
    assert "catalogue" not in keyless.json()
    missing = await client.post(f"{S}/{keyless.json()['id']}/behaviour-definition/reread")
    assert missing.status_code == 409, missing.text
    assert missing.json()["error"]["code"] == "behaviour_key_missing"


async def test_the_mcp_tool_carries_a_refusal_with_its_reason(
    client: httpx.AsyncClient,
    operator_name: str,
    catalogue: FakeMiWorkbench,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    versions = built_against_v1()
    created = await client.post(S, json=set_body(versions, name="mcp-refused", behaviour_key=KEY))
    mcp, mcp_client = production_mcp(monkeypatch)
    try:
        with pytest.raises(ToolError) as refused:
            await call(
                mcp, "dataworks_reread_behaviour_definition", {"set_id": created.json()["id"]}
            )
    finally:
        await mcp_client.close()
    code, status, details = refusal_of(refused.value)
    assert (code, status) == ("behaviour_not_in_catalogue", 409)
    assert details["next"]["tool"] == "dataworks_resolve_behaviour"


async def test_a_key_given_by_update_says_where_its_definition_came_from(
    client: httpx.AsyncClient, operator_name: str, catalogue: FakeMiWorkbench
) -> None:
    """Review minor 3: an update does not read the catalogue (the policy is the operator's); its
    answer says the definition is the training version's and names the re-read."""
    catalogue.behaviours[KEY] = catalogue_head(KEY, version=2, positive_meaning=V2_MEANING)
    versions = built_against_v1()
    created = await client.post(
        S, json=set_body(versions, name="keyed-later", behaviour_key="another-key")
    )
    assert created.status_code == 201, created.text
    assert created.json()["behaviour_definition"] is None
    moved = await client.patch(f"{S}/{created.json()['id']}", json={"behaviour_key": KEY})
    assert moved.status_code == 200, moved.text
    out = moved.json()
    assert out["behaviour_definition"] == BUILT_V1
    assert out["definition_from"] == "training_version"
    assert out["next"]["tool"] == "dataworks_reread_behaviour_definition"
    assert out["next"]["args"] == {"set_id": created.json()["id"]}
    plain = await client.patch(f"{S}/{created.json()['id']}", json={"description": "x"})
    assert "definition_from" not in plain.json(), "an update that records nothing says nothing"


# --- G20-edit (operator, 2026-11-08): a set's FIRST key by update takes the current definition ---

CATALOGUE_READS = "/api/v1/probe-monitors/behaviours/"


async def test_a_first_key_by_update_takes_the_catalogues_current_definition(
    client: httpx.AsyncClient, operator_name: str, catalogue: FakeMiWorkbench
) -> None:
    catalogue.behaviours[KEY] = catalogue_head(KEY, version=2, positive_meaning=V2_MEANING)
    versions = built_against_v1()
    created = await client.post(S, json=set_body(versions, name="keyless", positive_meaning=""))
    assert created.status_code == 201, created.text
    assert created.json()["behaviour_key"] is None
    before = catalogue.count("GET", CATALOGUE_READS)
    moved = await client.patch(f"{S}/{created.json()['id']}", json={"behaviour_key": KEY})
    assert moved.status_code == 200, moved.text
    out = moved.json()
    assert catalogue.count("GET", CATALOGUE_READS) - before == 1
    assert out["definition_from"] == "catalogue"
    assert out["behaviour_definition"]["definition_version"] == 2, "the v1 snapshot was taken"
    assert out["behaviour_definition"]["source"] == "catalogue"
    assert out["positive_meaning"] == V2_MEANING
    assert "next" not in out


async def test_a_set_that_had_a_key_keeps_its_own_rule_on_update(
    client: httpx.AsyncClient, operator_name: str, catalogue: FakeMiWorkbench
) -> None:
    """No catalogue read for a set that already had a key: a plain edit keeps its definition, and
    a key change follows 017's rule (the training version's, never another key's), naming the
    re-read."""
    catalogue.behaviours[KEY] = catalogue_head(KEY, version=2, positive_meaning=V2_MEANING)
    catalogue.behaviours["another-key"] = catalogue_head("another-key", version=5)
    versions = built_against_v1()
    created = (
        await client.post(S, json=set_body(versions, name="had-key", behaviour_key=KEY))
    ).json()
    assert created["behaviour_definition"]["definition_version"] == 2
    catalogue.behaviours[KEY] = catalogue_head(KEY, version=3, positive_meaning="v3 meaning")
    before = catalogue.count("GET", CATALOGUE_READS)
    edited = (await client.patch(f"{S}/{created['id']}", json={"description": "x"})).json()
    assert edited["behaviour_definition"]["definition_version"] == 2
    changed = (
        await client.patch(f"{S}/{created['id']}", json={"behaviour_key": "another-key"})
    ).json()
    assert catalogue.count("GET", CATALOGUE_READS) == before, "an update read the catalogue"
    assert changed["behaviour_definition"] is None, "the catalogue's v5 was taken"
    assert changed["definition_from"] == "none"
    assert changed["next"]["tool"] == "dataworks_reread_behaviour_definition"


async def test_a_first_key_with_an_unreadable_catalogue_is_refused_and_changes_nothing(
    client: httpx.AsyncClient, operator_name: str, catalogue: FakeMiWorkbench
) -> None:
    catalogue.behaviours[KEY] = catalogue_head(KEY, version=2, positive_meaning=V2_MEANING)
    versions = built_against_v1()
    created = (await client.post(S, json=set_body(versions, name="keyless-unread"))).json()
    catalogue.fail(ROUTE, drop=True)
    refused = await client.patch(
        f"{S}/{created['id']}", json={"behaviour_key": KEY, "description": "changed"}
    )
    assert refused.status_code == 502, refused.text
    assert refused.json()["error"]["code"] == "behaviour_catalogue_unread"
    after = (await client.get(f"{S}/{created['id']}")).json()
    assert after["behaviour_key"] is None and after["description"] == created["description"]
    assert after["behaviour_definition"] == created["behaviour_definition"]


async def test_a_first_key_standalone_takes_the_training_versions_and_says_why(
    client: httpx.AsyncClient, operator_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "mistudio_base_url", None)
    versions = built_against_v1()
    created = (await client.post(S, json=set_body(versions, name="keyless-standalone"))).json()
    moved = (await client.patch(f"{S}/{created['id']}", json={"behaviour_key": KEY})).json()
    assert moved["behaviour_definition"] == BUILT_V1
    assert moved["definition_from"] == "training_version"
    assert moved["catalogue"] == "miWorkbench is not configured"
    assert "next" not in moved
