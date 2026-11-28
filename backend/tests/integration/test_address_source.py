"""Where each sibling address came from (feature 020, FR-020.6; FTASKS 3.7, 4.11).

The Environment listing names the variable an operator sets (the NEW name), the old one, and which
of the two supplied the value; health says per sibling whether the address in force is an
override, the new name, the old name, or unset, so a stored override cannot silently cancel a
cutover (FTDD §11.3).
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest

from src.core import env_names
from src.core.config import get_settings
from tests.support.env_rows import in_transition


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    in_transition(monkeypatch)  # the rows are retired since P7; these tests drive the fallback
    for row in env_names.RENAMES:
        monkeypatch.delenv(row.new, raising=False)
        monkeypatch.delenv(row.old, raising=False)
    get_settings.cache_clear()  # type: ignore[attr-defined]
    yield monkeypatch
    get_settings.cache_clear()  # type: ignore[attr-defined]


def _reload() -> None:
    get_settings.cache_clear()  # type: ignore[attr-defined]
    get_settings()


async def test_environment_listing_names_the_new_variable_and_which_supplied(
    env: pytest.MonkeyPatch, client: httpx.AsyncClient
) -> None:
    env.setenv("MILLM_BASE_URL", "http://old.test:8000")
    env.setenv("MIWORKBENCH_BASE_URL", "http://new.test:8000")
    _reload()
    body = (await client.get("/api/v1/settings/environment")).json()
    rows = {r["key"]: r for r in body["settings"]}
    millm = rows["millm_base_url"]
    assert millm["env_var"] == "MISERVER_BASE_URL"
    assert millm["env_var_old"] == "MILLM_BASE_URL"
    assert millm["env_var_read"] == "MILLM_BASE_URL"
    # The date itself is test_environment_listing_serves_the_retire_date's subject; here only
    # that the row carries what the module holds (None until P4 closes, 15.5).
    assert millm["retire_after"] == (
        env_names.RETIRE_AFTER.isoformat() if env_names.RETIRE_AFTER else None
    )
    mistudio = rows["mistudio_base_url"]
    assert mistudio["env_var"] == "MIWORKBENCH_BASE_URL"
    assert mistudio["env_var_old"] == "MISTUDIO_BASE_URL"
    assert mistudio["env_var_read"] == "MIWORKBENCH_BASE_URL"
    unset = rows["millm_ui_url"]
    assert unset["env_var"] == "MISERVER_UI_URL" and unset["env_var_read"] is None
    plain = rows["log_level"]
    assert plain["env_var"] == "LOG_LEVEL" and plain["env_var_old"] is None


async def test_environment_listing_serves_the_retire_date(
    env: pytest.MonkeyPatch, client: httpx.AsyncClient
) -> None:
    """FTASKS 15.5: the Environment card shows ``RETIRE_AFTER`` beside an old name in use. The
    listing serves the module's value, whatever it is: a date as ISO text on every renamed row,
    None when unset, and never on a row that was not renamed."""
    from datetime import date

    env.setenv("MILLM_BASE_URL", "http://old.test:8000")
    _reload()
    for value, served in ((date(2027, 1, 15), "2027-01-15"), (None, None)):
        env.setattr(env_names, "RETIRE_AFTER", value)
        body = (await client.get("/api/v1/settings/environment")).json()
        rows = {r["key"]: r for r in body["settings"]}
        assert rows["millm_base_url"]["retire_after"] == served
        assert rows["mistudio_base_url"]["retire_after"] == served
        assert rows["log_level"]["retire_after"] is None


async def test_restart_text_names_the_real_variable(
    env: pytest.MonkeyPatch, client: httpx.AsyncClient
) -> None:
    """``millm_lease_holder`` is deployment configuration: the refusal tells the operator which
    variable to change, by its new name."""
    response = await client.put("/api/v1/settings/millm_lease_holder", json={"value": "x"})
    assert response.status_code == 422, response.text
    message = response.json()["error"]["message"]
    assert "MISERVER_LEASE_HOLDER" in message and "MILLM_LEASE_HOLDER" not in message


# --- 4.11: health says where each sibling address came from -----------------------------------

#: Addresses that refuse at once (health probes the siblings).
OLD = "http://127.0.0.1:9"
NEW = "http://127.0.0.1:7"


async def _deps(client: httpx.AsyncClient) -> dict[str, dict[str, object]]:
    response = await client.get("/api/health")
    assert response.status_code == 200, response.text
    deps: dict[str, dict[str, object]] = response.json()["dependencies"]
    return deps


async def test_override_row_reports_override(
    env: pytest.MonkeyPatch, client: httpx.AsyncClient
) -> None:
    env.setenv("MISERVER_BASE_URL", NEW)
    _reload()
    put = await client.put("/api/v1/settings/millm_base_url", json={"value": OLD})
    assert put.status_code == 200, put.text
    deps = await _deps(client)
    assert deps["millm"]["address_source"] == "override"
    assert deps["mistudio"]["address_source"] == "unset"


async def test_old_name_reports_env_legacy(
    env: pytest.MonkeyPatch, client: httpx.AsyncClient
) -> None:
    env.setenv("MILLM_BASE_URL", OLD)
    env.setenv("MISTUDIO_BASE_URL", OLD)
    _reload()
    deps = await _deps(client)
    assert deps["millm"]["address_source"] == "env_legacy"
    assert deps["mistudio"]["address_source"] == "env_legacy"


async def test_new_name_reports_env(env: pytest.MonkeyPatch, client: httpx.AsyncClient) -> None:
    env.setenv("MISERVER_BASE_URL", NEW)
    env.setenv("MILLM_BASE_URL", OLD)  # both set: the new one won, so the source is env
    env.setenv("MIWORKBENCH_BASE_URL", NEW)
    _reload()
    deps = await _deps(client)
    assert deps["millm"]["address_source"] == "env"
    assert deps["mistudio"]["address_source"] == "env"


async def test_unset_reports_unset(env: pytest.MonkeyPatch, client: httpx.AsyncClient) -> None:
    deps = await _deps(client)
    assert deps["millm"]["address_source"] == "unset"
    assert deps["mistudio"]["address_source"] == "unset"
    assert deps["millm"]["previous_origins"] == [] and deps["mistudio"]["previous_origins"] == []
    assert deps["millm"]["roles_on_previous_address"] == []
    assert "roles_on_previous_address" not in deps["mistudio"]


async def test_previous_origins_are_listed(
    env: pytest.MonkeyPatch, client: httpx.AsyncClient
) -> None:
    env.setenv("MISERVER_PREVIOUS_BASE_URLS", "http://z-old.test:8000,http://a-old.test:8000/")
    env.setenv("MIWORKBENCH_PREVIOUS_BASE_URLS", "https://bench-old.test")
    _reload()
    deps = await _deps(client)
    assert deps["millm"]["previous_origins"] == ["http://a-old.test:8000", "http://z-old.test:8000"]
    assert deps["mistudio"]["previous_origins"] == ["https://bench-old.test:443"]
