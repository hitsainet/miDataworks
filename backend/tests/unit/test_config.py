"""Configuration fails loudly, naming what is missing (Foundation task 3.1)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.core import env_names
from src.core.config import REQUIRED_VARIABLES, ConfigurationError, Settings, load_settings

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture
def full_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h:1/midataworks_test")
    monkeypatch.setenv("REDIS_URL", "redis://h:2/0")
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY", "k" * 32)
    monkeypatch.setenv("INTERNAL_API_SECRET", "internal-secret")
    return monkeypatch


@pytest.mark.parametrize("variable", REQUIRED_VARIABLES)
def test_a_missing_required_variable_fails_naming_it(
    full_env: pytest.MonkeyPatch, variable: str
) -> None:
    full_env.delenv(variable)
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert f"{variable} is required and not set" in str(excinfo.value)


def test_all_required_present_loads(full_env: pytest.MonkeyPatch) -> None:
    full_env.delenv("DATA_DIR", raising=False)
    settings = load_settings()
    assert settings.data_dir == Path("/data/dataworks")
    assert settings.progress_heartbeat_seconds == 60.0


def test_a_short_encryption_key_is_refused(full_env: pytest.MonkeyPatch) -> None:
    full_env.setenv("SETTINGS_ENCRYPTION_KEY", "too-short")
    with pytest.raises(ConfigurationError, match="SETTINGS_ENCRYPTION_KEY"):
        load_settings()


def test_a_relative_data_dir_is_refused(full_env: pytest.MonkeyPatch) -> None:
    full_env.setenv("DATA_DIR", "data/dataworks")
    with pytest.raises(ConfigurationError, match="DATA_DIR"):
        load_settings()


def test_an_unparsable_boolean_is_refused_not_read_as_false(full_env: pytest.MonkeyPatch) -> None:
    full_env.setenv("CELERY_TASK_ALWAYS_EAGER", "maybe")
    with pytest.raises(ConfigurationError, match="CELERY_TASK_ALWAYS_EAGER"):
        load_settings()


def test_an_absent_boolean_takes_its_default(full_env: pytest.MonkeyPatch) -> None:
    full_env.delenv("CELERY_TASK_ALWAYS_EAGER", raising=False)
    assert load_settings().celery_task_always_eager is False


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgresql+asyncpg://u:p@h:1/d", "postgresql+psycopg2://u:p@h:1/d"),
        ("postgresql://u:p@h:1/d", "postgresql+psycopg2://u:p@h:1/d"),
    ],
)
def test_the_sync_url_is_derived_from_the_async_one(
    full_env: pytest.MonkeyPatch, url: str, expected: str
) -> None:
    full_env.setenv("DATABASE_URL", url)
    assert load_settings().database_url_sync == expected


def test_env_example_lists_every_variable_the_settings_read() -> None:
    """Feature 020 (ID-14): a renamed field is listed under its NEW name, the one the operator
    sets; deriving from ``name.upper()`` would force the old name back as an assignment."""
    example = (REPO / ".env.example").read_text()
    listed = set(re.findall(r"^([A-Z][A-Z0-9_]+)=", example, re.M))
    declared = {env_names.env_var_for(name) for name in Settings.model_fields}
    assert declared <= listed, f"missing from .env.example: {sorted(declared - listed)}"


def test_env_example_lists_old_names_only_as_comments() -> None:
    """The old names stay documented until P7, but only as comments: an assignment would set a
    name that stops being read (FR-020.4, FR-020.25)."""
    example = (REPO / ".env.example").read_text()
    for row in env_names.RENAMES:
        assert not re.search(rf"^{row.old}=", example, re.M), f"{row.old} is assigned"
        if not row.retired:
            assert re.search(
                rf"^# {row.old}= \(old name, read until P7\)", example, re.M
            ), f"{row.old} is not listed as an old-name comment"


@pytest.mark.parametrize("field", ["miserver_previous_base_urls", "miworkbench_previous_base_urls"])
def test_previous_base_urls_parse_to_origins(full_env: pytest.MonkeyPatch, field: str) -> None:
    """020 TPD-6: a comma list of origins of the SAME instance; spelling variants collapse to one
    origin, order is kept, blanks are dropped."""
    full_env.setenv(
        field.upper(),
        " http://old.test:8000/ , HTTP://Old.Test:8000,,https://other.test ,http://old.test:8000",
    )
    assert getattr(load_settings(), field) == ("http://old.test:8000", "https://other.test:443")


def test_previous_base_urls_default_to_empty(full_env: pytest.MonkeyPatch) -> None:
    settings = load_settings()
    assert settings.miserver_previous_base_urls == ()
    assert settings.miworkbench_previous_base_urls == ()


def test_previous_base_url_with_a_path_is_refused(full_env: pytest.MonkeyPatch) -> None:
    full_env.setenv("MISERVER_PREVIOUS_BASE_URLS", "http://old.test:8000/v1")
    with pytest.raises(ConfigurationError, match="MISERVER_PREVIOUS_BASE_URLS.*origin"):
        load_settings()


def test_previous_base_url_with_credentials_is_refused(full_env: pytest.MonkeyPatch) -> None:
    full_env.setenv("MIWORKBENCH_PREVIOUS_BASE_URLS", "http://user:pw@old.test:8000")
    with pytest.raises(ConfigurationError, match="MIWORKBENCH_PREVIOUS_BASE_URLS"):
        load_settings()


def test_feature_002_settings_default_as_documented(full_env: pytest.MonkeyPatch) -> None:
    """Task 19.1: the five settings of FTDD 002 section 11, with their documented defaults."""
    settings = load_settings()
    assert settings.version_build_janitor_minutes == 30
    assert settings.row_event_copy_chunk == 50_000
    assert settings.parquet_row_group_rows == 100_000
    assert settings.row_history_text_matches == 50
    assert settings.orphan_sweep_age_minutes == 60
