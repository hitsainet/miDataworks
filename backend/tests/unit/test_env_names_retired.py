"""A retired old name set alone is not configuration, and it is reported (feature 020; FR-020.26,
PD-12; FTASKS 19.2, control M34).

This REPLACES the old-only test of FTASKS 3.1 (``test_old_name_only_is_read_and_reported_env_legacy``,
FR-020.4): that test asserted the fallback; from the export that retires a name (P7) the same input
must be refused. Driven by the export's ``env`` records, never by a hand-kept list: each record that
is ``retired`` is set ALONE in the environment with a value that differs from the field's default,
so "not read" is visible in the value, not only in the report (a fixture agreeing by construction
would pass against a loader that still read it).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from src.clients.miworkbench_client import MiWorkbenchClient, MiWorkbenchUnreachable
from src.core import env_names, legacy_names
from src.core.config import Settings, get_settings, load_settings
from src.services import health_service
from tests.support import legacy_identifiers as li


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    for row in env_names.RENAMES:
        monkeypatch.delenv(row.new, raising=False)
        monkeypatch.delenv(row.old, raising=False)
    get_settings.cache_clear()  # type: ignore[attr-defined]
    yield monkeypatch
    get_settings.cache_clear()  # type: ignore[attr-defined]
    load_settings()  # leave this process's report as the environment has it


def _other_than_default(field: str) -> str:
    """A valid value for ``field`` that its default is not."""
    default = Settings.model_fields[field].default
    if isinstance(default, bool):
        return str(not default).lower()
    if isinstance(default, int | float):
        return str(int(default) + 7)
    if isinstance(default, str):
        return default + "-old"
    return "http://old-name.test:8000"


def _retired_rows() -> list[env_names.EnvRename]:
    retired = {r["old"] for r in li.load().section("env") if r["status"] == "retired"}
    return [row for row in env_names.RENAMES if row.old in retired]


class _UnreadableDb:
    """The health block's database, down: ``retired_but_set`` comes from this process alone."""

    async def execute(self, *_: Any, **__: Any) -> Any:
        raise ConnectionError("no database in a unit test")


async def test_retired_old_name_alone_is_not_configured_and_reported(
    env: pytest.MonkeyPatch,
) -> None:
    rows = _retired_rows()
    assert rows, "the export retires no env record yet: this is P7's test (FR-020.26)"
    for row in rows:
        env.setenv(row.old, _other_than_default(row.field))
    settings = load_settings()

    for row in rows:
        assert getattr(settings, row.field) == Settings.model_fields[row.field].default, row.old
    report = env_names.current_report()
    assert report.retired_but_set == tuple(row.old for row in rows)
    assert not set(report.read) & {row.field for row in rows}, report.read

    # Health (PD-12, BR-023): listed under retired_but_set, never as a name in use.
    names, _meta = await legacy_names.block(_UnreadableDb())
    assert names["retired_but_set"] == [row.old for row in rows]
    assert names["in_use"] == []

    # FR-020.26: a retired MILLM_BASE_URL alone leaves miServer not configured, and a retired
    # MISTUDIO_BASE_URL alone leaves miWorkbench refusing by the NEW variable's name.
    retired_fields = {row.field for row in rows}
    if "millm_base_url" in retired_fields:
        dependency = await health_service.check_http(settings.millm_base_url, "/api/health")
        assert dependency["configured"] is False and dependency["ok"] is False
    if "mistudio_base_url" in retired_fields:
        with pytest.raises(MiWorkbenchUnreachable, match=r"\(MIWORKBENCH_BASE_URL\)"):
            MiWorkbenchClient(settings.mistudio_base_url or "")
