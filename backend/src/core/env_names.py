"""Sibling environment names in transition (feature 020, part A; FTID §3.1, FTDD §4.4).

The suite renamed its inference server to miServer and its workbench to miWorkbench (ENH-009; the
old environment prefixes are the ``old`` column below). miDataworks keeps every
Settings field name and runtime key (PD-2); only the ENVIRONMENT VARIABLE names change. Each
renamed field reads its new name first and its old name while the row is not retired, so a pod
started from an unchanged ConfigMap keeps working through the whole rename. Since P7 every row is
retired: only the new names are read, and an old one still set is reported.

What this module guarantees:
- ``RENAMES`` is a literal, hand-kept table (CF-3). It reads no file: the generated export
  ``docs/legacy-identifiers.md`` is a second, independent source, and
  ``tests/unit/test_env_names_match_export.py`` compares the two (a real check, never agreement by
  construction).
- No other backend module reads a ``MILLM_``/``MISTUDIO_``/``MISERVER_``/``MIWORKBENCH_``
  variable (``tests/unit/test_env_names.py::test_no_sibling_env_read_outside_env_names``).
- Names only: the report says WHICH variable supplied a field, never its value.

What it refuses: nothing here raises. A retired old name still set is reported in
``retired_but_set`` and read as nothing (the field keeps its default and the integration refuses,
naming the new variable).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class EnvRename:
    """One renamed environment variable. ``field`` is the Settings field, unchanged (PD-2)."""

    field: str
    new: str
    old: str
    retired: bool = False


#: The seven rows of FTDD §4.4. A literal: this module never reads the export (CF-3, CF-25 m13).
#: P7 (FTASKS 19.3, 2026-11-13): every row is retired on its own evidence (S001 7.06: no process read
#: the old name after the sweep) and the export's ``env`` records say ``retired``; the old names are
#: reported when set and never read (FR-020.25, FR-020.26). Rows stay so the report can name them.
RENAMES: tuple[EnvRename, ...] = (
    EnvRename("millm_base_url", "MISERVER_BASE_URL", "MILLM_BASE_URL", retired=True),
    EnvRename("millm_ui_url", "MISERVER_UI_URL", "MILLM_UI_URL", retired=True),
    EnvRename(
        "millm_lease_ttl_seconds",
        "MISERVER_LEASE_TTL_SECONDS",
        "MILLM_LEASE_TTL_SECONDS",
        retired=True,
    ),
    EnvRename("millm_lease_holder", "MISERVER_LEASE_HOLDER", "MILLM_LEASE_HOLDER", retired=True),
    EnvRename(
        "millm_steering_supported",
        "MISERVER_STEERING_SUPPORTED",
        "MILLM_STEERING_SUPPORTED",
        retired=True,
    ),
    EnvRename("mistudio_base_url", "MIWORKBENCH_BASE_URL", "MISTUDIO_BASE_URL", retired=True),
    EnvRename(
        "mistudio_timeout_seconds",
        "MIWORKBENCH_TIMEOUT_SECONDS",
        "MISTUDIO_TIMEOUT_SECONDS",
        retired=True,
    ),
)

#: The date shown beside an old name in Settings ("rename it before ..."). Set by the commit that
#: brings the export with P4 closed (TPD-18: P4 close + 30 days; a close on 2026-11-11). If P4 closes
#: on another day, correct it: ``tests/unit/test_env_names.py`` only requires a date from then on.
RETIRE_AFTER: date | None = date(2026, 12, 11)

_BY_FIELD: dict[str, EnvRename] = {row.field: row for row in RENAMES}


@dataclass(frozen=True)
class EnvReport:
    """Which environment name supplied each renamed field in this process. Names only."""

    read: dict[str, str] = field(default_factory=dict)
    also_set: tuple[EnvRename, ...] = ()
    conflicts: tuple[EnvRename, ...] = ()
    retired_but_set: tuple[str, ...] = ()


_report: EnvReport = EnvReport()


def _value(view: Mapping[str, str], name: str) -> str | None:
    raw = view.get(name.upper())
    if raw is None or not raw.strip():
        return None
    return raw


def resolve(environ: Mapping[str, str]) -> tuple[dict[str, str], EnvReport]:
    """Return the init-argument fallbacks and the report for one environment. Pure.

    New name set: pydantic-settings reads it natively (the field's ``validation_alias``); the old
    name, if also set, is reported in ``also_set`` (and ``conflicts`` when the values differ).
    New name unset and the old one set on a live row: the old value becomes an init argument.
    A retired row's old name is reported in ``retired_but_set`` and read as nothing. Empty or
    whitespace-only values count as unset, as ``Settings._sibling_address`` treats them.
    """
    view = {str(k).upper(): str(v) for k, v in environ.items()}
    init: dict[str, str] = {}
    read: dict[str, str] = {}
    also_set: list[EnvRename] = []
    conflicts: list[EnvRename] = []
    retired_but_set: list[str] = []
    for row in RENAMES:
        new_value = _value(view, row.new)
        old_value = _value(view, row.old)
        if new_value is not None:
            read[row.field] = row.new
            if old_value is not None:
                also_set.append(row)
                if old_value.strip() != new_value.strip():
                    conflicts.append(row)
            continue
        if old_value is None:
            continue
        if row.retired:
            retired_but_set.append(row.old)
            continue
        init[row.field] = old_value
        read[row.field] = row.old
    report = EnvReport(
        read=read,
        also_set=tuple(also_set),
        conflicts=tuple(conflicts),
        retired_but_set=tuple(retired_but_set),
    )
    return init, report


def env_var_for(field_name: str) -> str:
    """The environment variable an operator sets for a Settings field (the new name)."""
    row = _BY_FIELD.get(field_name)
    return row.new if row is not None else field_name.upper()


def setting_name(field_name: str) -> str:
    """What a refusal tells the operator to set (FR-020.7): the new variable name."""
    return env_var_for(field_name)


def old_name_for(field_name: str) -> str | None:
    """The old variable name of a renamed field, else None."""
    row = _BY_FIELD.get(field_name)
    return row.old if row is not None else None


def field_for_alias(name: str) -> str | None:
    """The Settings field whose new or old variable is ``name`` (any case), else None."""
    upper = name.upper()
    for row in RENAMES:
        if upper in (row.new, row.old):
            return row.field
    return None


def current_report() -> EnvReport:
    """The report of this process's last ``load_settings()``; empty before the first load."""
    return _report


def _set_report(report: EnvReport) -> None:
    """Record the report of a settings load (``core.config.load_settings`` only)."""
    global _report
    _report = report
