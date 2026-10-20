"""An endpoint role's key is tied to its endpoint (operator decision 2026-10-20).

Settings: moving a role's base URL to another origin (scheme, host, port) clears the stored key
unless the same request supplies one, and the answer says so plainly. Senders: every client that
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


# --- Settings: clear on change ------------------------------------------------------------------


async def test_moving_the_url_clears_the_key_and_says_so(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    first = await _put(client, "judge", base_url=ORIGIN + "/v1", api_key=KEY)
    assert first.json()["key_origin"] == "http://millm.test:80"
    assert first.json()["key_cleared"] is False and first.json()["key_notice"] is None

    moved = await _put(client, "judge", base_url=OTHER + "/v1", api_key=None)
    body = moved.json()
    assert moved.status_code == 200
    assert body["key_cleared"] is True and body["has_api_key"] is False
    assert body["api_key"] is None and body["key_origin"] is None
    notice = body["key_notice"]
    assert "cleared" in notice and "http://millm.test:80" in notice
    assert "http://elsewhere.test:80" in notice and "Enter the key again" in notice
    assert "switching back" in notice
    assert _stored("judge") == (OTHER + "/v1", None, None)

    back = await _put(client, "judge", base_url=ORIGIN + "/v1", api_key=None)
    assert back.json()["has_api_key"] is False, "switching back does not restore the key"
    assert back.json()["key_cleared"] is False


async def test_a_masked_key_does_not_survive_a_move(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    await _put(client, "judge", base_url=ORIGIN + "/v1", api_key=KEY)
    masked = (await client.get("/api/v1/endpoint-roles/judge")).json()["api_key"]
    moved = await _put(client, "judge", base_url=OTHER + "/v1", api_key=masked)
    assert moved.json()["key_cleared"] is True
    assert _stored("judge")[1] is None


@pytest.mark.parametrize(
    "new_url",
    [
        "https://millm.test/v1",  # scheme
        "http://millm.test:8080/v1",  # port
        "http://other.millm.test/v1",  # host
        "",  # no server at all
    ],
)
async def test_any_origin_change_clears(
    client: httpx.AsyncClient, labeling: Labeling, new_url: str
) -> None:
    await _put(client, "judge", base_url=ORIGIN + "/v1", api_key=KEY)
    moved = await _put(client, "judge", base_url=new_url, api_key=None)
    assert moved.json()["key_cleared"] is True
    assert _stored("judge")[1] is None


@pytest.mark.parametrize(
    "same", ["http://millm.test/v2", "http://MILLM.Test:80/v1", "http://millm.test/v1/"]
)
async def test_the_same_origin_keeps_the_key(
    client: httpx.AsyncClient, labeling: Labeling, same: str
) -> None:
    await _put(client, "judge", base_url=ORIGIN + "/v1", api_key=KEY)
    before = _stored("judge")[1]
    kept = await _put(client, "judge", base_url=same, api_key=None)
    assert kept.json()["key_cleared"] is False and kept.json()["has_api_key"] is True
    assert _stored("judge")[1] == before


async def test_a_new_key_in_the_same_request_is_kept_and_bound_to_the_new_server(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    await _put(client, "judge", base_url=ORIGIN + "/v1", api_key=KEY)
    moved = await _put(client, "judge", base_url=OTHER + "/v1", api_key="sk-other-key-987654321")
    body = moved.json()
    assert body["key_cleared"] is False and body["has_api_key"] is True
    assert body["key_origin"] == "http://elsewhere.test:80"
    assert _stored("judge")[2] == "http://elsewhere.test:80"


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
