"""Independent review of 020 P0: every one of the seven renamed variables, through the real loader.

The existing tests exercise three of the seven rows by value. Here each row is set under its OLD
name alone (the production ConfigMap today) and must reach Settings and be recorded as an old name
in use; then both names carry DIFFERENT values and the new one must win (control V01, and the
per-row control R-ROW, which drops one row's fallback)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from src.core import env_names, legacy_names
from src.core.config import get_settings
from tests.support.env_rows import in_transition

#: (old-name value, new-name value, what Settings holds for each), per field. Every new value
#: differs from the old one and from the field's default, so neither can win by coincidence.
VALUES: dict[str, tuple[str, str, object, object]] = {
    "millm_base_url": ("http://old.test:8000", "http://new.test:8000", None, None),
    "millm_ui_url": ("https://old-ui.test", "https://new-ui.test", None, None),
    "millm_lease_ttl_seconds": ("901", "902", 901, 902),
    "millm_lease_holder": ("holder-old", "holder-new", None, None),
    "millm_steering_supported": ("false", "true", False, True),
    "mistudio_base_url": ("http://old-bench.test:8000", "http://new-bench.test:8000", None, None),
    "mistudio_timeout_seconds": ("41", "42", 41.0, 42.0),
}


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    in_transition(monkeypatch)  # the rows are retired since P7; these tests drive the fallback
    for row in env_names.RENAMES:
        monkeypatch.delenv(row.new, raising=False)
        monkeypatch.delenv(row.old, raising=False)
    get_settings.cache_clear()  # type: ignore[attr-defined]
    yield monkeypatch
    get_settings.cache_clear()  # type: ignore[attr-defined]


def _expected(field: str, which: int) -> object:
    raw, typed = VALUES[field][which], VALUES[field][2 + which]
    return raw if typed is None else typed


def test_the_table_covers_every_row() -> None:
    assert {row.field for row in env_names.RENAMES} == set(VALUES)


@pytest.mark.parametrize("row", env_names.RENAMES, ids=lambda r: r.old)
def test_old_name_alone_reaches_settings_and_is_recorded(
    env: pytest.MonkeyPatch, row: env_names.EnvRename
) -> None:
    env.setenv(row.old, VALUES[row.field][0])
    get_settings.cache_clear()  # type: ignore[attr-defined]
    assert getattr(get_settings(), row.field) == _expected(row.field, 0)
    assert env_names.current_report().read == {row.field: row.old}
    assert [r.old for r in legacy_names.names_in_use_here()] == [row.old]


@pytest.mark.parametrize("row", env_names.RENAMES, ids=lambda r: r.new)
def test_new_name_wins_over_a_different_old_value(
    env: pytest.MonkeyPatch, row: env_names.EnvRename
) -> None:
    env.setenv(row.old, VALUES[row.field][0])
    env.setenv(row.new, VALUES[row.field][1])
    get_settings.cache_clear()  # type: ignore[attr-defined]
    assert getattr(get_settings(), row.field) == _expected(row.field, 1)
    report = env_names.current_report()
    assert report.read == {row.field: row.new}
    assert [r.old for r in report.conflicts] == [row.old]
    assert legacy_names.names_in_use_here() == []
