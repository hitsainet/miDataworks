"""An endpoint role's key is tied to its endpoint (operator decisions 2026-10-20).

Settings (feature 014, replacing "clear on move"): keys are stored per origin (scheme, host, port).
Moving a role's base URL keeps the old key for the old server and brings back the key stored for
the new one, and the answer says so plainly. Senders: every client that
sends a role key checks the key's stored origin against the URL it is about to call, so a row
whose stored origin disagrees with its base URL sends NO key on any path.

Each client path is asserted on the fake server's recorded headers, both ways: bound (the key
arrives, so the path passes ``key_origin`` through) and unbound (no key arrives).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from src.clients import endpoint_caller, model_listing
from src.core.database import get_sync_engine
from src.core.key_binding import url_origin
from tests.integration.labeling.helpers import QUESTION, setup_classifier, start_body
from tests.support.fake_millm import ORIGIN, TEI_ORIGIN, FakeMillm, route
from tests.support.labeling_fixtures import KEY, Labeling, set_role

OTHER = "http://elsewhere.test"
BEARER = f"Bearer {KEY}"
SCORING_PATHS = ("/v1/completions", "/v1/chat/completions")


@pytest.fixture
def other(labeling: Labeling) -> Iterator[FakeMillm]:
    """A second server at another origin, beside the fixture's miLLM and TEI."""
    server = FakeMillm()
    previous = endpoint_caller.install_transport(
        route({ORIGIN: labeling.millm, TEI_ORIGIN: labeling.tei, OTHER: server})
    )
    yield server
    endpoint_caller.install_transport(previous)


def _stored(role: str) -> tuple[str | None, str | None, str | None]:
    with get_sync_engine().connect() as conn:
        row = conn.execute(
            text(
                "SELECT base_url, api_key_ciphertext, api_key_origin FROM dw_endpoint_roles "
                "WHERE role = :r"
            ),
            {"r": role},
        ).one()
    return row[0], row[1], row[2]


def _auth(requests: list[Any]) -> list[str | None]:
    return [r.headers.get("authorization") for r in requests]


async def _put(client: httpx.AsyncClient, role: str, **body: Any) -> httpx.Response:
    base = {"protocol": "openai_chat", "model_id": "q"}
    return await client.put(f"/api/v1/endpoint-roles/{role}", json={**base, **body})


# --- Settings: one key per server (feature 014, operator directive 2026-10-20) -------------------
#
# The earlier rule cleared the key on a move ("switching back needs it entered again"). The operator
# replaced it: a key SURVIVES a move away and back, and still goes only to its own server.

OTHER_KEY = "sk-other-server-key-987654321"


def _parked() -> dict[str, str]:
    with get_sync_engine().connect() as conn:
        rows = conn.execute(
            text(
                "SELECT origin, api_key_ciphertext FROM dw_endpoint_role_keys WHERE role = 'judge'"
            )
        ).all()
    return {r[0]: r[1] for r in rows}


async def test_moving_away_keeps_the_key_for_its_own_server_and_says_so(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    first = await _put(client, "judge", base_url=ORIGIN + "/v1", api_key=KEY)
    assert first.json()["key_origin"] == "http://millm.test:80"
    assert first.json()["key_notice"] is None and first.json()["key_parked"] is False
    ciphertext = _stored("judge")[1]
    assert ciphertext is not None

    moved = await _put(client, "judge", base_url=OTHER + "/v1", api_key=None)
    body = moved.json()
    assert moved.status_code == 200
    assert body["key_parked"] is True and body["key_restored"] is False
    assert body["has_api_key"] is False and body["api_key"] is None and body["key_origin"] is None
    assert [(k["origin"], k["in_use"]) for k in body["stored_keys"]] == [
        ("http://millm.test:80", False)
    ]
    assert KEY not in moved.text, "a stored key is only ever shown masked"
    notice = body["key_notice"]
    assert "http://millm.test:80" in notice and "http://elsewhere.test:80" in notice
    assert "kept" in notice and "sent only there" in notice
    # Nothing in use; the ciphertext is kept byte for byte under its own origin.
    assert _stored("judge") == (OTHER + "/v1", None, None)
    assert _parked() == {"http://millm.test:80": ciphertext}


async def test_moving_back_restores_the_original_key(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    await _put(client, "judge", base_url=ORIGIN + "/v1", api_key=KEY)
    ciphertext = _stored("judge")[1]
    await _put(client, "judge", base_url=OTHER + "/v1", api_key=None)

    back = await _put(client, "judge", base_url=ORIGIN + "/v2", api_key=None)
    body = back.json()
    assert body["key_restored"] is True and body["has_api_key"] is True
    assert body["key_origin"] == "http://millm.test:80"
    assert "stored earlier for http://millm.test:80" in body["key_notice"]
    assert _stored("judge") == (ORIGIN + "/v2", ciphertext, "http://millm.test:80")
    assert _parked() == {}, "a key lives in one place: restored, it leaves the keyring"


async def test_a_masked_key_on_a_move_keeps_the_key_for_its_server(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    await _put(client, "judge", base_url=ORIGIN + "/v1", api_key=KEY)
    masked = (await client.get("/api/v1/endpoint-roles/judge")).json()["api_key"]
    moved = await _put(client, "judge", base_url=OTHER + "/v1", api_key=masked)
    assert moved.status_code == 200 and moved.json()["key_parked"] is True
    assert _stored("judge")[1] is None and "http://millm.test:80" in _parked()


@pytest.mark.parametrize(
    "new_url",
    [
        "https://millm.test/v1",  # scheme
        "http://millm.test:8080/v1",  # port
        "http://other.millm.test/v1",  # host
        "",  # no server at all
    ],
)
async def test_any_origin_change_parks_the_key(
    client: httpx.AsyncClient, labeling: Labeling, new_url: str
) -> None:
    await _put(client, "judge", base_url=ORIGIN + "/v1", api_key=KEY)
    moved = await _put(client, "judge", base_url=new_url, api_key=None)
    assert moved.json()["key_parked"] is True
    assert _stored("judge")[1] is None
    assert list(_parked()) == ["http://millm.test:80"]


@pytest.mark.parametrize(
    "same", ["http://millm.test/v2", "http://MILLM.Test:80/v1", "http://millm.test/v1/"]
)
async def test_the_same_origin_keeps_the_key(
    client: httpx.AsyncClient, labeling: Labeling, same: str
) -> None:
    await _put(client, "judge", base_url=ORIGIN + "/v1", api_key=KEY)
    before = _stored("judge")[1]
    kept = await _put(client, "judge", base_url=same, api_key=None)
    assert kept.json()["key_parked"] is False and kept.json()["has_api_key"] is True
    assert kept.json()["key_notice"] is None
    assert _stored("judge")[1] == before and _parked() == {}


async def test_each_server_keeps_its_own_key_through_moves(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    await _put(client, "judge", base_url=ORIGIN + "/v1", api_key=KEY)
    moved = await _put(client, "judge", base_url=OTHER + "/v1", api_key=OTHER_KEY)
    body = moved.json()
    assert body["has_api_key"] is True and body["key_origin"] == "http://elsewhere.test:80"
    assert body["key_parked"] is True and body["key_restored"] is False
    assert {(k["origin"], k["in_use"]) for k in body["stored_keys"]} == {
        ("http://elsewhere.test:80", True),
        ("http://millm.test:80", False),
    }
    back = (await _put(client, "judge", base_url=ORIGIN + "/v1", api_key=None)).json()
    assert back["key_restored"] is True and back["key_origin"] == "http://millm.test:80"
    assert list(_parked()) == ["http://elsewhere.test:80"]


async def test_a_new_key_replaces_the_one_kept_for_that_server(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    await _put(client, "judge", base_url=ORIGIN + "/v1", api_key=KEY)
    await _put(client, "judge", base_url=OTHER + "/v1", api_key=None)
    old = _parked()["http://millm.test:80"]
    back = await _put(client, "judge", base_url=ORIGIN + "/v1", api_key="sk-replacement-1234567")
    assert back.json()["key_restored"] is False and back.json()["has_api_key"] is True
    assert _stored("judge")[1] != old and _parked() == {}


async def test_an_empty_key_clears_only_this_servers_key(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    await _put(client, "judge", base_url=OTHER + "/v1", api_key=OTHER_KEY)
    await _put(client, "judge", base_url=ORIGIN + "/v1", api_key=KEY)
    cleared = await _put(client, "judge", base_url=ORIGIN + "/v1", api_key="")
    assert cleared.json()["has_api_key"] is False
    assert list(_parked()) == ["http://elsewhere.test:80"], "the other server's key is untouched"


async def test_a_key_needs_the_url_it_is_for(client: httpx.AsyncClient, labeling: Labeling) -> None:
    refused = await _put(client, "judge", base_url="", api_key=KEY)
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "KEY_NEEDS_URL"


async def test_the_operator_deletes_a_stored_key(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    await _put(client, "judge", base_url=ORIGIN + "/v1", api_key=KEY)
    await _put(client, "judge", base_url=OTHER + "/v1", api_key=OTHER_KEY)
    # A kept key, named by any URL on its server.
    gone = await client.delete(
        "/api/v1/endpoint-roles/judge/keys", params={"origin": ORIGIN + "/some/path"}
    )
    assert gone.status_code == 200
    assert [k["origin"] for k in gone.json()["stored_keys"]] == ["http://elsewhere.test:80"]
    assert _parked() == {}
    # The key in use, named by its origin.
    gone = await client.delete(
        "/api/v1/endpoint-roles/judge/keys", params={"origin": "http://elsewhere.test:80"}
    )
    assert gone.json()["stored_keys"] == [] and gone.json()["has_api_key"] is False
    assert _stored("judge")[1] is None
    missing = await client.delete("/api/v1/endpoint-roles/judge/keys", params={"origin": ORIGIN})
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "KEY_NOT_STORED"


async def test_an_agent_cannot_delete_a_stored_key(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    await _put(client, "judge", base_url=ORIGIN + "/v1", api_key=KEY)
    refused = await client.delete(
        "/api/v1/endpoint-roles/judge/keys",
        params={"origin": ORIGIN},
        headers={"X-Dataworks-Agent": "agent:dataworks-mcp"},
    )
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "AGENT_FORBIDDEN"
    assert _stored("judge")[1] is not None


# --- Senders: what reaches the wire -------------------------------------------------------------


async def test_after_a_move_the_endpoint_test_sends_no_key(
    client: httpx.AsyncClient, labeling: Labeling, other: FakeMillm
) -> None:
    await _put(client, "judge", base_url=ORIGIN + "/v1", api_key=KEY)
    await client.post("/api/v1/endpoint-roles/judge/test")
    assert labeling.millm.requests and set(_auth(labeling.millm.requests)) == {BEARER}

    await _put(client, "judge", base_url=OTHER + "/v1", api_key=None)
    await client.post("/api/v1/endpoint-roles/judge/test")
    assert other.requests, "the test reached the new server"
    assert set(_auth(other.requests)) == {None}


async def test_after_a_move_fetch_models_sends_no_key(
    client: httpx.AsyncClient, labeling: Labeling, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": [{"id": "q"}]})

    class Async(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            return handler(request)

    real = model_listing.fetch_models

    async def wired(*args: Any, **kwargs: Any) -> model_listing.ModelListing:
        return await real(*args, transport=Async(), **kwargs)

    from src.services import endpoint_role_service

    monkeypatch.setattr(endpoint_role_service, "fetch_models", wired)
    await _put(client, "judge", base_url=ORIGIN + "/v1", api_key=KEY)
    assert (await client.get("/api/v1/endpoint-roles/judge/models")).status_code == 200
    same_origin_override = await client.get(
        "/api/v1/endpoint-roles/judge/models", params={"base_url": ORIGIN + "/other/v1"}
    )
    other_override = await client.get(
        "/api/v1/endpoint-roles/judge/models", params={"base_url": OTHER + "/v1"}
    )
    assert same_origin_override.status_code == other_override.status_code == 200
    assert _auth(seen) == [BEARER, BEARER, None]

    seen.clear()
    await _put(client, "judge", base_url=OTHER + "/v1", api_key=None)
    assert (await client.get("/api/v1/endpoint-roles/judge/models")).status_code == 200
    assert _auth(seen) == [None]


async def _exercise(
    client: httpx.AsyncClient, labeling: Labeling, *, mismatch: bool
) -> dict[str, list[Any]]:
    """Every labeling client path, one at a time: the requests each one made."""
    from src.services.labeling_ports import PortLeaseManager, PortResolver
    from src.workers import label_run_tasks

    version_id, template_id = await setup_classifier(client, labeling, n=12)
    out: dict[str, list[Any]] = {}

    def take(name: str) -> None:
        out[name] = list(labeling.millm.requests)
        labeling.millm.requests.clear()

    # setup_classifier re-binds the role; tests that need a mismatch re-write it afterwards.
    if mismatch:
        set_role("classifier", key_origin=url_origin(OTHER))
    labeling.millm.requests.clear()

    await client.post("/api/v1/endpoint-roles/classifier/test")
    take("endpoint_test")
    run = (await client.post("/api/v1/label-runs", json=start_body(version_id, template_id))).json()
    take("run_start")
    labeling.run_until_done(run["id"])
    take("label_run")
    await client.post(
        "/api/v1/labeling/sample",
        json={
            "input_version_id": version_id,
            "role": "classifier",
            "template_id": template_id,
            "question": QUESTION,
            "field_map": {"text": "text"},
            "rows": 3,
        },
    )
    take("sample")
    preview = await client.post(
        "/api/v1/labeling/keep-share",
        json={
            "input_version_id": version_id,
            "template_id": template_id,
            "question": QUESTION,
            "field_map": {"text": "text"},
            "threshold_positive": 0.5,
            "threshold_negative": 0.2,
            "sample_rows": 6,
        },
    )
    label_run_tasks.preview_job(preview.json()["job_id"])
    take("keep_share")
    endpoint = PortResolver().resolve("classifier")
    take("operator_port_resolve")
    leases = PortLeaseManager()
    lease = leases.acquire(endpoint)
    assert lease is not None
    leases.renew(lease)
    leases.release(lease)
    take("operator_port_lease")
    return out


async def test_every_labeling_path_sends_the_bound_key(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    paths = await _exercise(client, labeling, mismatch=False)
    for name, requests in paths.items():
        assert requests, f"{name} made no request; the test would prove nothing"
        assert set(_auth(requests)) == {BEARER}, f"{name} did not send the bound key"
    scored = [r for r in paths["label_run"] if r.path in SCORING_PATHS]
    assert len(scored) >= 1


async def test_no_labeling_path_sends_a_key_bound_elsewhere(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    paths = await _exercise(client, labeling, mismatch=True)
    for name, requests in paths.items():
        assert requests, f"{name} made no request; the test would prove nothing"
        assert set(_auth(requests)) == {None}, f"{name} sent a key bound to another origin"


async def test_a_key_with_no_stored_origin_is_never_sent(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    set_role("judge", protocol="openai_chat", model="q", key_origin=None)
    await client.post("/api/v1/endpoint-roles/judge/test")
    assert labeling.millm.requests and set(_auth(labeling.millm.requests)) == {None}


async def test_the_judge_borrowing_the_classifier_endpoint_uses_the_classifier_binding(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    set_role("classifier")
    set_role(
        "judge", protocol="openai_chat", model="q", api_key=None, use_mode="same_as_classifier"
    )
    await client.post("/api/v1/endpoint-roles/judge/test")
    assert set(_auth(labeling.millm.requests)) == {BEARER}
    labeling.millm.requests.clear()
    set_role("classifier", key_origin=url_origin(OTHER))
    await client.post("/api/v1/endpoint-roles/judge/test")
    assert labeling.millm.requests and set(_auth(labeling.millm.requests)) == {None}


# --- The operator's scenario on the wire (feature 014) -------------------------------------------


async def test_move_away_and_back_sends_each_key_only_to_its_own_server(
    client: httpx.AsyncClient, labeling: Labeling, other: FakeMillm
) -> None:
    """TypeSafe -> miLLM -> TypeSafe, with miLLM's own key in between: the first key reaches only
    its server, before and after the round trip; the second only its own; neither crosses."""
    await _put(client, "judge", base_url=ORIGIN + "/v1", api_key=KEY)
    await client.post("/api/v1/endpoint-roles/judge/test")
    assert labeling.millm.requests and set(_auth(labeling.millm.requests)) == {BEARER}

    # Away, with no key for the new server: nothing is sent there.
    await _put(client, "judge", base_url=OTHER + "/v1", api_key=None)
    await client.post("/api/v1/endpoint-roles/judge/test")
    assert other.requests and set(_auth(other.requests)) == {None}

    # The other server's own key goes to it, and only to it.
    await _put(client, "judge", base_url=OTHER + "/v1", api_key=OTHER_KEY)
    other.requests.clear()
    await client.post("/api/v1/endpoint-roles/judge/test")
    assert other.requests and set(_auth(other.requests)) == {f"Bearer {OTHER_KEY}"}

    # Back: the ORIGINAL key is sent again, without being re-entered.
    labeling.millm.requests.clear()
    await _put(client, "judge", base_url=ORIGIN + "/v1", api_key=None)
    await client.post("/api/v1/endpoint-roles/judge/test")
    assert labeling.millm.requests and set(_auth(labeling.millm.requests)) == {BEARER}

    # Never across: each server saw only its own key or none, over the whole sequence.
    assert f"Bearer {OTHER_KEY}" not in _auth(labeling.millm.requests)
    assert BEARER not in _auth(other.requests)


async def test_workers_resolve_the_restored_key_and_no_key_while_away(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    """The worker paths read the role row: away -> no key; back -> the original key."""
    from src.core.database import sync_session_factory
    from src.services.endpoint_resolver import resolve
    from src.workers.secrets import resolve_endpoint_key

    await _put(client, "judge", base_url=ORIGIN + "/v1", api_key=KEY)
    await _put(client, "judge", base_url=OTHER + "/v1", api_key=None)
    with sync_session_factory()() as db:
        assert resolve_endpoint_key(db, "judge") is None
        assert resolve("judge", db).api_key is None
    await _put(client, "judge", base_url=ORIGIN + "/v1", api_key=None)
    with sync_session_factory()() as db:
        assert resolve_endpoint_key(db, "judge") == KEY
        resolved = resolve("judge", db)
        assert resolved.api_key == KEY and resolved.key_origin == "http://millm.test:80"


async def test_fetch_models_sends_the_key_kept_for_the_server_it_asks(
    client: httpx.AsyncClient, labeling: Labeling, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Trying a URL before saving it (Fetch models with base_url): the key stored for THAT server,
    whether in use or kept, and no key for a server with none."""
    seen: list[httpx.Request] = []

    class Async(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"data": [{"id": "q"}]})

    real = model_listing.fetch_models

    async def wired(*args: Any, **kwargs: Any) -> model_listing.ModelListing:
        return await real(*args, transport=Async(), **kwargs)

    from src.services import endpoint_role_service

    monkeypatch.setattr(endpoint_role_service, "fetch_models", wired)
    await _put(client, "judge", base_url=ORIGIN + "/v1", api_key=KEY)
    await _put(client, "judge", base_url=OTHER + "/v1", api_key=OTHER_KEY)
    for url in (ORIGIN + "/v1", OTHER + "/v1", "http://third.test/v1"):
        response = await client.get("/api/v1/endpoint-roles/judge/models", params={"base_url": url})
        assert response.status_code == 200
    assert _auth(seen) == [BEARER, f"Bearer {OTHER_KEY}", None]
    assert [r.url.host for r in seen] == ["millm.test", "elsewhere.test", "third.test"]
