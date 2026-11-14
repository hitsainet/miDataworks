"""A re-send after a cutover inserts no registration (feature 020; FR-020.16; FTASKS 6.16)."""

from __future__ import annotations

import httpx
import pytest

from tests.integration.detector_sets.test_send_manifest import resend
from tests.integration.test_previous_origin_lookups import _registrations, _sent_then_switched
from tests.support.publish_fixtures import publisher  # noqa: F401 - fixture
from tests.support.send_fixtures import SendDriver, sender  # noqa: F401 - fixture
from tests.support.version_fixtures import driver  # noqa: F401 - fixture


async def test_second_send_after_switch_inserts_no_registration(
    client: httpx.AsyncClient,
    operator_name: str,
    sender: SendDriver,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_id = await _sent_then_switched(client, sender, monkeypatch)
    before = _registrations()
    second = await resend(client, sender, set_id)
    assert sender.send(second).state == "completed", sender.send(second).error
    assert _registrations() == before
