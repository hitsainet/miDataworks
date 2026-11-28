"""Refusals name the NEW sibling variable (feature 020, FR-020.7; FTASKS 3.5).

An operator told to set ``MISTUDIO_BASE_URL`` after the rename would set a name that stops being
read at P7. Every refusal message and ``details.setting`` is built through
``core.env_names.setting_name``; the CODES stay (register DR-18) and the product names in the
prose change only in P4 (TPD-15).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from src.core.config import get_settings
from tests.integration.detector_sets.test_probe_verdict_runs import (  # noqa: F401 - fixtures
    body,
    millm,
    refreshed,
    rows,
)
from tests.support.labeling_fixtures import Labeling, labeling, make_version  # noqa: F401
from tests.support.publish_fixtures import publisher  # noqa: F401 - fixture
from tests.support.send_fixtures import API, SendDriver, sender  # noqa: F401 - fixture
from tests.support.version_fixtures import driver  # noqa: F401 - fixture

OLD_NAMES = ("MISTUDIO_BASE_URL", "MILLM_BASE_URL")


def _names_only_the_new(text: str, new: str) -> None:
    assert new in text, text
    for old in OLD_NAMES:
        assert old not in text, text


async def test_send_refusal_names_miworkbench_base_url(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.services.detector_sets import integration
    from src.services.detector_sets.errors import DetectorSetError

    monkeypatch.setattr(get_settings(), "mistudio_base_url", None)
    with pytest.raises(DetectorSetError) as raised:
        integration.require_mistudio("Sending a detector set")
    assert raised.value.code == "mistudio_not_configured"
    _names_only_the_new(raised.value.message, "MIWORKBENCH_BASE_URL")
    assert raised.value.details["setting"] == "MIWORKBENCH_BASE_URL"

    response = await client.post(
        f"{API}/reward-marks", json={"mistudio_probe_id": "pm_x", "reason": "trained on it"}
    )
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "mistudio_not_configured"
    assert error["details"]["setting"] == "MIWORKBENCH_BASE_URL"
    _names_only_the_new(error["message"], "MIWORKBENCH_BASE_URL")


async def test_probe_refusal_names_miserver_base_url(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "millm_base_url", None)
    response = await client.get(f"{API}/labeling/probes")
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "PROBE_ENDPOINT_UNCONFIGURED"
    assert error["details"] == {"setting": "MISERVER_BASE_URL"}
    _names_only_the_new(error["message"], "MISERVER_BASE_URL")


async def test_reproduction_link_refusal_names_miworkbench_base_url(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, operator_name: str
) -> None:
    monkeypatch.setattr(get_settings(), "mistudio_base_url", None)
    response = await client.post(
        f"{API}/reproduction-links",
        json={
            "mistudio_probe_id": "pm_x",
            "probe_dataset_id": "pd_x",
            "version_id": "00000000-0000-0000-0000-000000000000",
            "split": "test",
        },
    )
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "mistudio_not_configured"
    assert error["details"]["setting"] == "MIWORKBENCH_BASE_URL"
    _names_only_the_new(error["message"], "MIWORKBENCH_BASE_URL")


async def test_endpoint_changed_message_names_miserver_base_url(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    millm: Labeling,  # noqa: F811
    sender: SendDriver,  # noqa: F811
) -> None:
    """A probe run started on one miServer address and resumed on an UNRELATED one refuses
    ``ENDPOINT_CHANGED``; the message names the variable to look at by its new name."""
    await refreshed(client, sender)
    version_id = make_version(data_dir, rows(4))
    started = await client.post(f"{API}/label-runs", json=body(version_id))
    assert started.status_code == 201, started.text
    get_settings().millm_base_url = "http://other.test:8000"
    final = millm.run_until_done(started.json()["id"])
    assert final.state == "failed"
    assert final.error is not None and final.error["code"] == "ENDPOINT_CHANGED"
    _names_only_the_new(final.error["message"], "MISERVER_BASE_URL")
