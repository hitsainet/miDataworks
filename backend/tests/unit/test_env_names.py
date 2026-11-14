"""Sibling environment names in transition (feature 020 part A; FR-020.4, FR-020.7; FTASKS 3.1).

Fixtures never agree by construction: the old and the new name carry DIFFERENT values whenever both
are set, so "the new one won" is visible in the value, not only in the report.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from src.core import env_names
from src.core.config import ConfigurationError, Settings, get_settings, load_settings

SRC = Path(__file__).resolve().parents[2] / "src"
SIBLING_PREFIXES = ("MILLM_", "MISTUDIO_", "MISERVER_", "MIWORKBENCH_")


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    """Every renamed variable unset; settings reloaded before and after the test."""
    for row in env_names.RENAMES:
        monkeypatch.delenv(row.new, raising=False)
        monkeypatch.delenv(row.old, raising=False)
    get_settings.cache_clear()  # type: ignore[attr-defined]
    yield monkeypatch
    get_settings.cache_clear()  # type: ignore[attr-defined]


def _reload() -> Settings:
    get_settings.cache_clear()  # type: ignore[attr-defined]
    return get_settings()


@pytest.fixture
def transition(env: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Every row back in transition. Since P7 every real row is retired, so the tests of the
    fallback's own rules (blank values, case, error naming) put the rows back to be read at all."""
    rows = tuple(env_names.EnvRename(r.field, r.new, r.old) for r in env_names.RENAMES)
    env.setattr(env_names, "RENAMES", rows)
    env.setattr(env_names, "_BY_FIELD", {row.field: row for row in rows})
    return env


def test_old_name_only_is_not_read_once_retired(env: pytest.MonkeyPatch) -> None:
    """FTASKS 19.3: the old-only test of 3.1 in its retired form. It asserted the fallback
    (``MILLM_BASE_URL`` alone was read); since P7 the same input is refused and reported. The full
    form, driven by the export, is ``test_env_names_retired.py`` (FR-020.26)."""
    env.setenv("MILLM_BASE_URL", "http://a.test:8000")
    env.setenv("MISTUDIO_TIMEOUT_SECONDS", "41")
    settings = _reload()
    assert settings.millm_base_url is None
    assert settings.mistudio_timeout_seconds == 30.0
    report = env_names.current_report()
    assert "millm_base_url" not in report.read and "mistudio_timeout_seconds" not in report.read
    assert report.retired_but_set == ("MILLM_BASE_URL", "MISTUDIO_TIMEOUT_SECONDS")
    assert report.also_set == () and report.conflicts == ()


def test_a_row_in_transition_reads_its_old_name(env: pytest.MonkeyPatch) -> None:
    """The fallback itself (FR-020.4) is still the code path a row in transition takes; every row
    is retired since P7, so a row is put back in transition here to keep it held."""
    rows = tuple(
        env_names.EnvRename(r.field, r.new, r.old, retired=r.field != "millm_base_url")
        for r in env_names.RENAMES
    )
    env.setattr(env_names, "RENAMES", rows)
    env.setenv("MILLM_BASE_URL", "http://a.test:8000")
    env.setenv("MISTUDIO_TIMEOUT_SECONDS", "41")
    settings = _reload()
    assert settings.millm_base_url == "http://a.test:8000"
    assert settings.mistudio_timeout_seconds == 30.0
    report = env_names.current_report()
    assert report.read["millm_base_url"] == "MILLM_BASE_URL"
    assert report.retired_but_set == ("MISTUDIO_TIMEOUT_SECONDS",)


def test_new_name_only_is_read_and_reported_new(env: pytest.MonkeyPatch) -> None:
    env.setenv("MISERVER_BASE_URL", "http://new.test:8000")
    settings = _reload()
    assert settings.millm_base_url == "http://new.test:8000"
    assert env_names.current_report().read["millm_base_url"] == "MISERVER_BASE_URL"


def test_both_set_different_new_wins_and_conflict_reported(env: pytest.MonkeyPatch) -> None:
    env.setenv("MILLM_BASE_URL", "http://old.test:8000")
    env.setenv("MISERVER_BASE_URL", "http://new.test:8000")
    settings = _reload()
    assert settings.millm_base_url == "http://new.test:8000"
    report = env_names.current_report()
    assert report.read["millm_base_url"] == "MISERVER_BASE_URL"
    assert [r.old for r in report.also_set] == ["MILLM_BASE_URL"]
    assert [r.old for r in report.conflicts] == ["MILLM_BASE_URL"]


def test_both_set_equal_is_also_set_only(env: pytest.MonkeyPatch) -> None:
    env.setenv("MISTUDIO_BASE_URL", "http://same.test:8000")
    env.setenv("MIWORKBENCH_BASE_URL", "http://same.test:8000")
    _reload()
    report = env_names.current_report()
    assert [r.old for r in report.also_set] == ["MISTUDIO_BASE_URL"]
    assert report.conflicts == ()


def test_empty_value_is_unset(transition: pytest.MonkeyPatch) -> None:
    env = transition
    env.setenv("MISERVER_BASE_URL", "   ")
    env.setenv("MILLM_BASE_URL", "http://old.test:8000")
    env.setenv("MIWORKBENCH_BASE_URL", "")
    env.setenv("MISTUDIO_BASE_URL", "")
    settings = _reload()
    assert settings.millm_base_url == "http://old.test:8000", "a blank new name does not win"
    assert settings.mistudio_base_url is None
    report = env_names.current_report()
    assert report.read == {"millm_base_url": "MILLM_BASE_URL"}
    assert report.also_set == ()


def test_lookup_is_case_insensitive_as_settings_is(transition: pytest.MonkeyPatch) -> None:
    init, report = env_names.resolve({"millm_base_url": "http://a.test"})
    assert init == {"millm_base_url": "http://a.test"}
    assert report.read == {"millm_base_url": "MILLM_BASE_URL"}


def test_rename_table_new_equals_field_alias() -> None:
    for row in env_names.RENAMES:
        info = Settings.model_fields[row.field]
        assert info.validation_alias == row.new, row.field


def test_every_millm_or_mistudio_field_has_a_row() -> None:
    sibling_fields = {
        name for name in Settings.model_fields if name.startswith(("millm_", "mistudio_"))
    }
    assert len(sibling_fields) >= 7, "the field list was not read"
    assert sibling_fields == {row.field for row in env_names.RENAMES}


def test_retired_row_reads_nothing_and_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    retired = tuple(
        env_names.EnvRename(r.field, r.new, r.old, retired=r.field == "millm_ui_url")
        for r in env_names.RENAMES
    )
    monkeypatch.setattr(env_names, "RENAMES", retired)
    init, report = env_names.resolve({"MILLM_UI_URL": "https://old.test"})
    assert init == {} and report.read == {}
    assert report.retired_but_set == ("MILLM_UI_URL",)


async def test_an_override_round_trips_by_field_name(
    env: pytest.MonkeyPatch, client: httpx.AsyncClient
) -> None:
    """An override row is keyed by the FIELD name (PD-2); with the alias set, validating the
    overlay by field name needs ``validate_by_name=True`` (control M19)."""
    env.setenv("MILLM_BASE_URL", "http://old.test:8000")
    _reload()
    put = await client.put(
        "/api/v1/settings/millm_base_url", json={"value": "http://override.test:8000"}
    )
    assert put.status_code == 200, put.text
    assert get_settings().millm_base_url == "http://override.test:8000"


@pytest.mark.parametrize(
    ("name", "other"),
    [("MILLM_BASE_URL", "MISERVER_BASE_URL"), ("MISERVER_BASE_URL", "MILLM_BASE_URL")],
)
def test_load_error_names_the_name_that_supplied_it(
    transition: pytest.MonkeyPatch, name: str, other: str
) -> None:
    """ID-13: a bad value is reported under the variable the operator actually set."""
    env = transition
    env.setenv(name, "ftp://bad.test")
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    message = str(excinfo.value)
    assert f"{name}:" in message
    assert other not in message


def _sibling_env_reads(tree: ast.AST) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        literal: object = None
        if isinstance(node, ast.Call):
            func = node.func
            name = ast.unparse(func)
            if name.endswith(("getenv", "environ.get", "environ.pop", "environ.setdefault")):
                if node.args and isinstance(node.args[0], ast.Constant):
                    literal = node.args[0].value
        elif isinstance(node, ast.Subscript) and ast.unparse(node.value).endswith("environ"):
            if isinstance(node.slice, ast.Constant):
                literal = node.slice.value
        if isinstance(literal, str) and literal.upper().startswith(SIBLING_PREFIXES):
            found.append((node.lineno, literal))
    return found


def test_no_sibling_env_read_outside_env_names() -> None:
    files = sorted(SRC.rglob("*.py"))
    assert len(files) > 100, "the source tree was not found"
    offenders = []
    for path in files:
        if path == SRC / "core" / "env_names.py":
            continue
        for line, name in _sibling_env_reads(ast.parse(path.read_text(), str(path))):
            offenders.append(f"{path.relative_to(SRC)}:{line} reads {name}")
    assert offenders == [], offenders


def test_the_env_read_tripwire_finds_a_planted_read() -> None:
    """Fail closed: the scanner sees each read form it claims to see."""
    planted = ast.parse(
        "import os\n"
        "a = os.environ.get('MILLM_BASE_URL')\n"
        "b = os.getenv('miworkbench_base_url')\n"
        "c = os.environ['MISERVER_UI_URL']\n"
    )
    assert [name for _, name in _sibling_env_reads(planted)] == [
        "MILLM_BASE_URL",
        "miworkbench_base_url",
        "MISERVER_UI_URL",
    ]


def test_the_old_name_is_never_read_natively(transition: pytest.MonkeyPatch) -> None:
    """The old names reach Settings only through ``resolve`` (so a retired one can stop being
    read). With ``validate_by_name`` pydantic would read the field name too; the env source
    hides it. Control: drop ``_NewNamesOnlyEnvSource`` and this is red."""
    env = transition
    env.setenv("MILLM_BASE_URL", "http://old.test:8000")
    assert Settings().millm_base_url is None
    assert load_settings().millm_base_url == "http://old.test:8000"


def test_load_error_names_an_old_name_that_differs_from_the_field(
    env: pytest.MonkeyPatch,
) -> None:
    """Control M50 survived at first: every old name here equals its field name upper-cased, so a
    loader that printed ``loc.upper()`` named the right variable by coincidence (a fixture agreeing
    by construction). A row whose old name differs from the field proves the mapping (ID-13)."""
    rows = tuple(
        env_names.EnvRename(
            r.field, r.new, "LEGACY_LLM_URL" if r.field == "millm_base_url" else r.old
        )
        for r in env_names.RENAMES
    )
    env.setattr(env_names, "RENAMES", rows)
    env.setattr(env_names, "_BY_FIELD", {row.field: row for row in rows})
    env.setenv("LEGACY_LLM_URL", "ftp://bad.test")
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "LEGACY_LLM_URL:" in str(excinfo.value)
    assert "MILLM_BASE_URL" not in str(excinfo.value)


# --- P4 (FTASKS 15.5; TPD-18, T3): the retire date is set when P4 closes, not before ------------


def retire_after_problem(closed: str | None, value: object) -> str | None:
    """What is wrong with ``RETIRE_AFTER`` given the export's ``closed`` phase, or None."""
    from datetime import date

    from tests.support import legacy_identifiers as li

    if value is not None and not isinstance(value, date):
        return f"RETIRE_AFTER is {type(value).__name__}, not a date"
    if not li.at_least(closed, "P4"):
        return None if value is None else "RETIRE_AFTER is set before the register closed P4"
    if value is None:
        return (
            "the register has closed P4 and RETIRE_AFTER is not set: set it to the P4 close date "
            "plus 30 days in the commit that brings the export with closed P4 (T3)"
        )
    return None


def test_retire_after_is_set_at_p4() -> None:
    """Keyed to the export's ``closed`` like the guard's mode (CF-21): None while P4 is open (the
    card says "before it is retired"), a date from the commit that records P4 closed. The P4 close
    date is S001's to record, so the value arrives with that commit and this test is red on it
    until it does."""
    from datetime import date

    from tests.support import legacy_identifiers as li

    assert retire_after_problem(li.load().header["closed"], env_names.RETIRE_AFTER) is None
    assert retire_after_problem("P3", None) is None
    assert "before the register closed P4" in str(retire_after_problem("P3", date(2026, 12, 15)))
    assert "RETIRE_AFTER is not set" in str(retire_after_problem("P4", None))
    assert "RETIRE_AFTER is not set" in str(retire_after_problem("P6", None))
    assert retire_after_problem("P4", date(2026, 12, 15)) is None
    assert "not a date" in str(retire_after_problem("P4", "2026-12-15"))
