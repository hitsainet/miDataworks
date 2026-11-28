"""Calls for rows stored under a previous miWorkbench origin go to the CURRENT address (feature 020;
FR-020.12; C1, C2; TPD-8; FTASKS 6.10).

The stored address is never rewritten (R1); only the address CALLED changes, and the rewrite is
logged with the two origins only. miWorkbench calls carry no key, so none can reach the new host."""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest

from src.clients import miworkbench_client
from tests.integration.detector_sets.test_results_and_marks import sent_set
from tests.integration.test_previous_origin_lookups import OLD, switch
from tests.support.publish_fixtures import publisher  # noqa: F401 - fixture
from tests.support.send_fixtures import (  # noqa: F401 - fixture
    API,
    SendDriver,
    ready_set,
    sender,
    start_send,
)
from tests.support.version_fixtures import driver  # noqa: F401 - fixture


def _record_hosts(drv: SendDriver, monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return drv.mistudio.handle(request)

    monkeypatch.setattr(miworkbench_client, "TRANSPORT", httpx.MockTransport(handler))
    return seen


async def test_resumed_send_calls_the_current_address(
    client: httpx.AsyncClient,
    operator_name: str,
    sender: SendDriver,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_id, _, _ = await ready_set(client, sender)
    started = await start_send(client, set_id)  # stored with the OLD address
    switch(monkeypatch)
    seen = _record_hosts(sender, monkeypatch)
    assert sender.run(started["job_id"])["status"] == "completed"
    send = sender.send(started["send_id"])
    assert send.mistudio_base_url == OLD, "the stored address is never rewritten"
    assert seen, "the send called nothing"
    assert {r.url.host for r in seen} == {"miworkbench.test"}
    assert seen[0].url.host == "miworkbench.test"


async def test_results_refresh_calls_the_current_address(
    client: httpx.AsyncClient,
    operator_name: str,
    sender: SendDriver,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_id, _ = await sent_set(client, sender)
    switch(monkeypatch)
    seen = _record_hosts(sender, monkeypatch)
    response = await client.post(f"{API}/detector-sets/{set_id}/results/refresh")
    assert response.status_code == 200, response.text
    assert seen and {r.url.host for r in seen} == {"miworkbench.test"}


async def test_no_authorization_header_reaches_the_new_host(
    client: httpx.AsyncClient,
    operator_name: str,
    sender: SendDriver,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_id, _, _ = await ready_set(client, sender)
    started = await start_send(client, set_id)
    switch(monkeypatch)
    seen = _record_hosts(sender, monkeypatch)
    sender.run(started["job_id"])
    assert seen and not [r for r in seen if "authorization" in r.headers]


async def test_rewrite_is_logged_with_origins_only(
    client: httpx.AsyncClient,
    operator_name: str,
    sender: SendDriver,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="src.core.sibling_origins")
    set_id, _, _ = await ready_set(client, sender)
    started = await start_send(client, set_id)
    switch(monkeypatch)
    sender.run(started["job_id"])
    lines: list[Any] = [
        r.getMessage()
        for r in caplog.records
        if "sibling_previous_origin_rewrite" in r.getMessage()
    ]
    assert len(lines) == 1, lines
    assert "from_origin=http://mistudio.test:80" in lines[0]
    assert "to_origin=http://miworkbench.test:80" in lines[0]
    assert "/api/" not in lines[0]
