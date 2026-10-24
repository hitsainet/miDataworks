"""The Environment listing on the Settings screen (feature 012; ENH-003 §2 "Consistent UI").

Every ``Settings`` field, in four groups:

* ``runtime`` - ``core.runtime_settings.RUNTIME_KEYS``: changeable while miDataworks runs;
* ``restart`` - needs a restart of the backend pod (stops every running job), read-only;
* ``approval_rule`` - the approval rules, deployment configuration (P-08, D11), read-only;
* ``environment`` - everything else, read-only.

**No secret value leaves this module.** A secret field is reported only as set or not set, and a
URL is shown with its password removed. ``tests/integration/test_runtime_settings_api.py`` puts
sentinel values in every secret and in the database password and searches the whole response.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from annotated_types import Ge, Gt, Le, Lt
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from ..core import runtime_settings as rs
from ..core.config import LOG_LEVELS, Settings, env_settings, get_settings
from ..schemas.settings import EnvironmentOut, EnvironmentRow, SettingChangeOut
from .app_setting_service import SETTINGS, AppSettingService, group_of, why_not_writable

#: Fields an agent never sees, even read-only (C5: the operator's own identity).
HIDDEN_FROM_AGENTS: frozenset[str] = frozenset({"operator_name"})


def redact_url(value: str) -> str:
    """``value`` with any password removed (``user:***@host``). Unparsable: returned unchanged
    only when it holds no ``@`` at all; otherwise fully hidden."""
    try:
        parts = urlsplit(value)
        if parts.password is None:
            return value
        host = parts.hostname or ""
        if parts.port is not None:
            host = f"{host}:{parts.port}"
        user = parts.username or ""
        return urlunsplit((parts.scheme, f"{user}:***@{host}", parts.path, parts.query, ""))
    except ValueError:
        return value if "@" not in value else "<hidden>"


def _shown(key: str, value: Any) -> Any:
    """A value as the listing may show it: never a secret, never a password."""
    if key in rs.SECRET_KEYS or isinstance(value, SecretStr):
        return None
    if isinstance(value, Path):
        return str(value)
    if key in rs.URL_KEYS and isinstance(value, str):
        return redact_url(value)
    return value


def _is_set(value: Any) -> bool:
    if isinstance(value, SecretStr):
        return bool(value.get_secret_value())
    return value is not None and value != ""


def _bounds(key: str) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for meta in Settings.model_fields[key].metadata:
        if isinstance(meta, Ge):
            out["minimum"] = float(meta.ge)  # type: ignore[arg-type]
        elif isinstance(meta, Gt):
            out["exclusive_minimum"] = float(meta.gt)  # type: ignore[arg-type]
        elif isinstance(meta, Le):
            out["maximum"] = float(meta.le)  # type: ignore[arg-type]
        elif isinstance(meta, Lt):
            out["exclusive_maximum"] = float(meta.lt)  # type: ignore[arg-type]
    return out


def _label(key: str) -> str:
    if key in rs.RUNTIME_KEYS:
        return rs.RUNTIME_KEYS[key].label
    return key.replace("_", " ").capitalize()


async def listing(db: AsyncSession, *, for_agent: bool = False) -> EnvironmentOut:
    env = env_settings()
    now = get_settings()
    stored = await AppSettingService.stored_overrides(db)
    last = await AppSettingService.last_changes(db)
    rows: list[EnvironmentRow] = []
    for key, field in Settings.model_fields.items():
        if for_agent and key in HIDDEN_FROM_AGENTS:
            continue
        group = group_of(key)
        secret = key in rs.SECRET_KEYS
        runtime = group == "runtime"
        spec = SETTINGS.get(key) if runtime else None
        rk = rs.RUNTIME_KEYS.get(key)
        change = last.get(key) if runtime else None
        rows.append(
            EnvironmentRow(
                key=key,
                env_var=key.upper(),
                label=_label(key),
                description=field.description or "",
                group=group,
                type=spec.type if spec is not None else "string",
                value=_shown(key, getattr(now, key)),
                env_value=_shown(key, getattr(env, key)),
                source="override" if runtime and key in stored else "env",
                runtime_writable=runtime,
                agent_writable=runtime and rk is not None and not rk.agent_forbidden,
                effect=rk.effect if rk is not None else None,
                effect_detail=(rk.effect_detail or None) if rk is not None else None,
                restart=why_not_writable(key) if group == "restart" else None,
                caution=(rk.caution or None) if rk is not None else None,
                secret=secret,
                is_set=_is_set(getattr(env, key)),
                choices=list(LOG_LEVELS) if key == "log_level" else None,
                updated_at=change.changed_at if change is not None else None,
                updated_by=change.actor if change is not None else None,
                **_bounds(key),
            )
        )
    order = {"runtime": 0, "restart": 1, "approval_rule": 2, "environment": 3}
    rows.sort(key=lambda r: (order[r.group], r.key))
    history = [
        SettingChangeOut(
            key=c.key,
            previous_value=c.previous_value,
            new_value=c.new_value,
            actor=c.actor,
            origin=c.origin,
            approval_id=c.approval_id,
            reason=c.reason,
            changed_at=c.changed_at,
        )
        for c in await AppSettingService.history(db)
        if not (for_agent and c.key in HIDDEN_FROM_AGENTS)
    ]
    return EnvironmentOut(settings=rows, history=history)
