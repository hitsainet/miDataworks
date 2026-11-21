"""Nothing points at a retired address before the old ones go (feature 020; FR-020.27; FTID §10.4;
FTASKS 19.4, controls M32 (P7 form) and M66).

``scripts/p7_check.py``'s ``p7_readiness`` judged over the REAL API's answers (``/api/health``,
``/api/v1/endpoint-roles``, ``/api/v1/settings``) with seeded rows, not over hand-built dicts: a
fixture shaped like the answer would agree with the check by construction. The live run (19.10) reads
the same three routes.

And the previous-address settings STAY after P7 (FR-020.27, PD-5, TPD-6): they resolve rows stored
with the old addresses (registrations, sends, reproduction links, paused runs), not live aliases.
Emptying them would hide every pre-cutover row and make the role flag above find nothing.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from scripts.p7_check import p7_readiness
from src.core import env_names
from src.core.config import get_settings
from src.core.key_binding import url_origin
from tests.support import legacy_identifiers as li
from tests.support.labeling_fixtures import set_role
from tests.unit import test_manifest_sibling_config as manifest

OLD = "http://millm-backend.millm.test:8000"
NEW = "http://miserver-backend.miserver.test:8000"
OLD_WB = "http://mistudio-backend.mistudio.test:8000"
NEW_WB = "http://miworkbench-backend.miworkbench.test:8000"


@pytest.fixture
def moved(monkeypatch: pytest.MonkeyPatch, clean_db: None) -> Iterator[None]:
    """Both siblings at their new addresses, each with its old one listed as previous."""
    settings = get_settings()
    monkeypatch.setattr(settings, "millm_base_url", NEW)
    monkeypatch.setattr(settings, "mistudio_base_url", NEW_WB)
    monkeypatch.setattr(settings, "miserver_previous_base_urls", (OLD,))
    monkeypatch.setattr(settings, "miworkbench_previous_base_urls", (OLD_WB,))
    yield


async def _answers(client: httpx.AsyncClient) -> tuple[dict[str, Any], Any, Any]:
    out = []
    for path in ("/api/health", "/api/v1/endpoint-roles", "/api/v1/settings"):
        response = await client.get(path)
        assert response.status_code == 200, (path, response.text)
        out.append(response.json())
    return out[0], out[1], out[2]


def _clean_roles() -> None:
    set_role("judge", base_url=NEW + "/v1", protocol="openai_chat")
    set_role("embeddings", base_url=NEW + "/v1", protocol="openai_chat")
    set_role("classifier", base_url="http://other.test:8000/v1")


async def test_p7_check_passes_when_clean(moved: None, client: httpx.AsyncClient) -> None:
    _clean_roles()
    health, roles, overrides = await _answers(client)
    assert len(roles) >= 3, "the roles were not read"
    assert p7_readiness(health, roles, overrides) == []


async def test_p7_check_fails_with_one_flagged_role(moved: None, client: httpx.AsyncClient) -> None:
    _clean_roles()
    set_role("judge", base_url=OLD + "/v1", protocol="openai_chat")
    health, roles, overrides = await _answers(client)
    reasons = p7_readiness(health, roles, overrides)
    assert len(reasons) == 1, reasons
    assert reasons[0].startswith("endpoint role judge is on a previous address"), reasons
    assert repr(NEW + "/v1") in reasons[0], "the reason names where to re-point it"


async def test_p7_check_fails_with_an_override_on_a_previous_origin(
    moved: None, client: httpx.AsyncClient
) -> None:
    _clean_roles()
    put = await client.put("/api/v1/settings/millm_ui_url", json={"value": OLD_WB + "/ui"})
    assert put.status_code == 200, put.text
    health, roles, overrides = await _answers(client)
    assert p7_readiness(health, roles, overrides) == [
        f"runtime override 'millm_ui_url' holds a previous origin {OLD_WB}; reset it or set the "
        "current address"
    ]
    # The current address itself overridden onto the old one: the override row AND the source.
    put = await client.put("/api/v1/settings/millm_base_url", json={"value": OLD})
    assert put.status_code == 200, put.text
    health, roles, overrides = await _answers(client)
    reasons = p7_readiness(health, roles, overrides)
    assert any(r.startswith("miServer: address_source is 'override'") for r in reasons), reasons
    assert any(r.startswith("runtime override 'millm_base_url'") for r in reasons), reasons


async def test_p7_check_fails_when_no_previous_origin_is_listed(
    moved: None, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a list empty the role flag cannot fire, so the check refuses rather than pass."""
    _clean_roles()
    set_role("judge", base_url=OLD + "/v1", protocol="openai_chat")
    monkeypatch.setattr(get_settings(), "miserver_previous_base_urls", ())
    health, roles, overrides = await _answers(client)
    assert not any(r.get("uses_previous_address") for r in roles), "the flag needs the list"
    reasons = p7_readiness(health, roles, overrides)
    assert reasons == [
        "miServer: no previous origins listed (dependencies.millm.previous_origins); nothing on "
        "an old address could be found, so this check would pass vacuously"
    ]


def test_alias_removal_leaves_previous_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """P7 retires the old variable NAMES (every ``env`` record of the export, every ``RENAMES``
    row); the previous-address settings are not renamed variables and keep their old origins in the
    ConfigMap, read by a pod built from it (FR-020.27; control M32)."""
    retired = {r["old"] for r in li.load().section("env") if r["status"] == "retired"}
    data = manifest._configmap(manifest._docs(manifest._tree_texts()))
    for key in manifest.PREVIOUS_KEYS:
        assert key not in {row.new for row in env_names.RENAMES} | retired, key
        assert data.get(key), f"{key} is gone from the ConfigMap: pre-cutover rows would hide"
    settings, report = manifest._settings_from(data, monkeypatch)
    assert list(settings.miserver_previous_base_urls) == [url_origin(manifest.MISERVER_OLD)]
    assert list(settings.miworkbench_previous_base_urls) == [url_origin(manifest.MIWORKBENCH_OLD)]
    assert report.retired_but_set == (), "the ConfigMap sets a retired old name"
