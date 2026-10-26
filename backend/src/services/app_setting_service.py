# Origin: miStudio (Onegaishimas/miStudio) backend/src/services/app_setting_service.py @ c829a2cc
# Mode: adapt (docs/REUSE.md). Kept: server-side sensitivity (a known secret is encrypted
# whatever the client says), masked reads, a row that cannot be authenticated shown as "***"
# and never as ciphertext, expunge-before-mutate on display paths. Changed: settings are a
# declared registry rather than free keys; an update carrying a MASKED value leaves the
# ciphertext untouched (task 8.4, the miStudio Session 13 defect); no boolean settings (the only
# one, hub_default_private, was removed unread on 2026-10-20); the settings PIN does not exist here.
# Feature 012 (2026-10-20, ENH-003 D10): the registry is also the store of the runtime environment
# overrides — one typed spec per allow-listed Settings field, validated by the Settings model — and
# every non-secret write appends to dw_setting_changes.
"""Settings read and write with mask handling (ADR-015; Foundation tasks 8.1, 8.4, 8.7; 012)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from types import NoneType, UnionType
from typing import Any, Literal, Union, get_args, get_origin

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core import runtime_settings
from ..core.config import SIBLING_ADDRESS_FIELDS, Settings, env_settings, get_settings
from ..core.encryption import DecryptionError, decrypt_value, encrypt_value, mask_value
from ..core.errors import AppError, NotFoundError
from ..models.app_setting import AppSetting
from ..models.setting_change import SettingChange

logger = logging.getLogger(__name__)

SettingType = Literal["string", "secret", "int", "float", "bool", "address", "choice"]


@dataclass(frozen=True)
class SettingSpec:
    key: str
    type: SettingType
    category: str
    description: str
    default: str | None = None
    #: Agents may neither read nor write it (C5: operator_name is the operator's own identity).
    agent_forbidden: bool = False
    #: An allow-listed environment setting (feature 012): the row overrides the environment.
    runtime: bool = False


def _runtime_type(key: str) -> SettingType:
    """The editor type of a runtime key, from the Settings field's own annotation."""
    annotation = Settings.model_fields[key].annotation
    args = [a for a in get_args(annotation) if a is not NoneType]
    if get_origin(annotation) in (Union, UnionType) and len(args) == 1:
        annotation = args[0]
    if key in SIBLING_ADDRESS_FIELDS:
        return "address"
    if key == "log_level":
        return "choice"
    if annotation is bool:
        return "bool"
    if annotation is int:
        return "int"
    if annotation is float:
        return "float"
    return "string"


def _runtime_spec(key: str) -> SettingSpec:
    field = Settings.model_fields[key]
    rk = runtime_settings.RUNTIME_KEYS[key]
    return SettingSpec(
        key,
        _runtime_type(key),
        "environment",
        field.description or rk.label,
        agent_forbidden=rk.agent_forbidden,
        runtime=True,
    )


#: Every setting the app reads. An unknown key is refused: a typo must not create a row that
#: nothing reads. Each key here needs a production reader; a key nothing reads is a promise the
#: app does not keep. (``hub_default_private`` was declared here and read by nothing; it only
#: restated FR-008.2 — a publish that omits visibility is private, in the UI, REST and MCP — so it
#: was removed on 2026-10-20 rather than wired, since a ``false`` would have broken FR-008.2.)
#: The runtime keys are generated from ``core.runtime_settings.RUNTIME_KEYS`` (feature 012); their
#: reader is ``get_settings()`` wherever the field is read.
SETTINGS: dict[str, SettingSpec] = {
    spec.key: spec
    for spec in (
        SettingSpec(
            "operator_name",
            "string",
            "identity",
            "Your name, recorded as who started a run or decided a review (C5).",
            agent_forbidden=True,
        ),
        SettingSpec(
            "hf_token",
            "secret",
            "api_keys",
            "Hugging Face token. Used only by workers; never logged or returned (ADR-015).",
        ),
        *(_runtime_spec(key) for key in runtime_settings.RUNTIME_KEYS),
    )
}

#: The two mask shapes core/encryption.mask_value produces.
_MASK_PATTERN = re.compile(r"^(\*\*\*|.{1,8}\.\.\..{1,8})$")


def is_masked_value(value: str) -> bool:
    """Does ``value`` look like a masked display string rather than a real secret?"""
    return bool(_MASK_PATTERN.fullmatch(value))


def get_spec(key: str) -> SettingSpec:
    spec = SETTINGS.get(key)
    if spec is not None:
        return spec
    if key in Settings.model_fields:
        raise AppError(
            f"{key} cannot be changed while miDataworks runs: {why_not_writable(key)}",
            code="NOT_RUNTIME_WRITABLE",
            status_code=422,
            details={"key": key, "group": group_of(key)},
        )
    raise NotFoundError(f"There is no setting called {key!r}.", code="UNKNOWN_SETTING")


def group_of(key: str) -> str:
    """runtime | restart | approval_rule | environment."""
    if key in runtime_settings.RUNTIME_KEYS:
        return "runtime"
    if key in runtime_settings.RESTART_KEYS:
        return "restart"
    if key in runtime_settings.APPROVAL_RULE_KEYS:
        return "approval_rule"
    return "environment"


def why_not_writable(key: str) -> str:
    group = group_of(key)
    if group == "restart":
        return (
            f"{runtime_settings.RESTART_KEYS[key]} Change {key.upper()} in the deployment and "
            f"restart the {runtime_settings.RESTART_POD} pod (this stops every running job)."
        )
    if group == "approval_rule":
        return (
            "the approval rules are deployment configuration (P-08); change "
            f"{key.upper()} in the deployment."
        )
    return f"it is set by the deployment ({key.upper()})."


@dataclass(frozen=True)
class SettingView:
    """A setting as the API shows it: secrets masked, never decrypted."""

    key: str
    value: str | None
    is_sensitive: bool
    is_set: bool
    category: str
    description: str
    type: SettingType
    #: Feature 012: where the value in force comes from, for a runtime key ("env" otherwise).
    source: Literal["env", "override"] = "env"
    #: The environment's value of a runtime key, as text ("" = not set); None for other keys.
    env_value: str | None = None
    effect: str | None = None
    runtime_writable: bool = False


def _display(spec: SettingSpec, row: AppSetting | None) -> SettingView:
    extra: dict[str, Any] = {}
    if spec.runtime:
        rk = runtime_settings.RUNTIME_KEYS[spec.key]
        extra = {
            "source": "override" if row is not None else "env",
            "env_value": runtime_settings.text_of(runtime_settings.env_value(spec.key)),
            "effect": rk.effect,
            "runtime_writable": True,
        }
    if row is None:
        return SettingView(
            spec.key,
            spec.default,
            spec.type == "secret",
            False,
            spec.category,
            spec.description,
            spec.type,
            **extra,
        )
    value = row.value
    if row.is_sensitive:
        try:
            value = mask_value(decrypt_value(row.value, setting_key=row.key))
        except DecryptionError:
            value = "***"
    return SettingView(
        spec.key,
        value,
        row.is_sensitive,
        True,
        spec.category,
        spec.description,
        spec.type,
        **extra,
    )


@dataclass(frozen=True)
class Author:
    """Who a settings change is recorded against (D8)."""

    actor: str
    origin: Literal["operator", "agent"]
    approval_id: str | None = None


class AppSettingService:
    @staticmethod
    async def _row(db: AsyncSession, key: str) -> AppSetting | None:
        return (
            await db.execute(select(AppSetting).where(AppSetting.key == key))
        ).scalar_one_or_none()

    @staticmethod
    async def stored_overrides(db: AsyncSession) -> dict[str, str]:
        rows = await db.execute(
            select(AppSetting.key, AppSetting.value).where(
                AppSetting.key.in_(list(runtime_settings.RUNTIME_KEYS))
            )
        )
        return {str(k): str(v) for k, v in rows.all()}

    @staticmethod
    async def list_views(db: AsyncSession, *, for_agent: bool = False) -> list[SettingView]:
        rows = {r.key: r for r in (await db.execute(select(AppSetting))).scalars()}
        return [
            _display(spec, rows.get(key))
            for key, spec in sorted(SETTINGS.items())
            if not (for_agent and spec.agent_forbidden and not spec.runtime)
        ]

    @staticmethod
    async def get_view(db: AsyncSession, key: str) -> SettingView:
        spec = get_spec(key)
        return _display(spec, await AppSettingService._row(db, key))

    @staticmethod
    async def get_plain(db: AsyncSession, key: str) -> str | None:
        """A non-secret setting's value, or its default. Secrets are refused here."""
        spec = get_spec(key)
        if spec.type == "secret":
            raise AppError("Secrets are not read through get_plain.", code="SECRET_NOT_READABLE")
        row = await AppSettingService._row(db, key)
        return row.value if row is not None else spec.default

    @staticmethod
    async def author(
        db: AsyncSession, origin: str, agent: str | None, approval_id: str | None
    ) -> Author:
        """The operator's recorded name (D8) or the agent identity, for the history."""
        if origin == "agent":
            return Author(agent or "agent:unknown", "agent", approval_id)
        row = await AppSettingService._row(db, "operator_name")
        name = (row.value if row is not None else "").strip()
        name = name or get_settings().operator_name.strip() or "operator"
        return Author(name, "operator", approval_id)

    @staticmethod
    def _record(
        db: AsyncSession,
        key: str,
        previous: str | None,
        new: str | None,
        author: Author | None,
        reason: str | None,
    ) -> None:
        if author is None:
            return
        db.add(
            SettingChange(
                key=key,
                previous_value=previous,
                new_value=new,
                actor=author.actor,
                origin=author.origin,
                approval_id=author.approval_id,
                reason=(reason or "").strip() or None,
            )
        )

    @staticmethod
    def validate_runtime(key: str, value: str, others: dict[str, str]) -> str:
        """The canonical stored text of ``value`` for runtime ``key``, or 422 with the reason."""
        try:
            return runtime_settings.validated_text(key, value, others=others)
        except runtime_settings.InvalidSettingValue as exc:
            raise AppError(
                f"{key} was not changed: {exc.message}",
                code="INVALID_SETTING_VALUE",
                status_code=422,
                details={"key": key},
            ) from None

    @staticmethod
    async def upsert(
        db: AsyncSession,
        key: str,
        value: str,
        *,
        author: Author | None = None,
        reason: str | None = None,
    ) -> tuple[SettingView, bool]:
        """Write a setting. Returns ``(view, changed)``.

        A masked value sent back for a secret (the UI re-saving a form it was shown) leaves the
        stored ciphertext byte-identical and returns ``changed=False``. A runtime key is validated
        by the Settings model with every other override in force (feature 012), and every
        non-secret write appends one history row when an ``author`` is given.
        """
        spec = get_spec(key)
        row = await AppSettingService._row(db, key)
        if spec.type == "secret":
            if is_masked_value(value):
                if row is None:
                    raise AppError(
                        "That looks like a masked value, and no secret is stored to keep. "
                        "Paste the real token.",
                        code="MASKED_VALUE_WITHOUT_SECRET",
                        status_code=422,
                    )
                return _display(spec, row), False
            stored, sensitive = encrypt_value(value), True
        elif spec.runtime:
            others = await AppSettingService.stored_overrides(db)
            stored, sensitive = AppSettingService.validate_runtime(key, value, others), False
        else:
            stored, sensitive = value.strip(), False
        previous = None if row is None or row.is_sensitive else row.value
        if row is None:
            row = AppSetting(key=key, value=stored, is_sensitive=sensitive, category=spec.category)
            db.add(row)
        else:
            row.value = stored
            row.is_sensitive = sensitive
            row.category = spec.category
        if not sensitive:
            AppSettingService._record(db, key, previous, stored, author, reason)
        await db.commit()
        await db.refresh(row)
        return _display(spec, row), True

    @staticmethod
    async def delete(
        db: AsyncSession,
        key: str,
        *,
        author: Author | None = None,
        reason: str | None = None,
    ) -> bool:
        get_spec(key)
        row = await AppSettingService._row(db, key)
        if row is None:
            return False
        if not row.is_sensitive:
            AppSettingService._record(db, key, row.value, None, author, reason)
        await db.delete(row)
        await db.commit()
        return True

    @staticmethod
    async def history(db: AsyncSession, limit: int = 50) -> list[SettingChange]:
        rows = await db.execute(
            select(SettingChange)
            .order_by(SettingChange.changed_at.desc(), SettingChange.id.desc())
            .limit(limit)
        )
        return list(rows.scalars())

    @staticmethod
    async def last_changes(db: AsyncSession) -> dict[str, SettingChange]:
        """The most recent change of each key."""
        rows = await db.execute(
            select(SettingChange).order_by(SettingChange.changed_at.desc(), SettingChange.id.desc())
        )
        latest: dict[str, SettingChange] = {}
        for change in rows.scalars():
            latest.setdefault(change.key, change)
        return latest


def env_text(key: str) -> str:
    return runtime_settings.text_of(getattr(env_settings(), key))


def changed_at(change: SettingChange | None) -> datetime | None:
    return change.changed_at if change is not None else None
