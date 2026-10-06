# Origin: miStudio (Onegaishimas/miStudio) backend/src/services/app_setting_service.py @ c829a2cc
# Mode: adapt (docs/REUSE.md). Kept: server-side sensitivity (a known secret is encrypted
# whatever the client says), masked reads, a row that cannot be authenticated shown as "***"
# and never as ciphertext, expunge-before-mutate on display paths. Changed: settings are a
# declared registry rather than free keys; an update carrying a MASKED value leaves the
# ciphertext untouched (task 8.4, the miStudio Session 13 defect); boolean settings fall back to
# their default, never to False; the settings PIN does not exist here.
"""Settings read and write with mask handling (ADR-015; Foundation tasks 8.1, 8.4, 8.7)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.encryption import DecryptionError, decrypt_value, encrypt_value, mask_value
from ..core.errors import AppError, NotFoundError
from ..models.app_setting import AppSetting

logger = logging.getLogger(__name__)

SettingType = Literal["string", "secret", "bool"]


@dataclass(frozen=True)
class SettingSpec:
    key: str
    type: SettingType
    category: str
    description: str
    default: str | None = None
    #: Agents may neither read nor write it (C5: operator_name is the operator's own identity).
    agent_forbidden: bool = False


#: Every setting the app reads. An unknown key is refused: a typo must not create a row that
#: nothing reads.
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
        SettingSpec(
            "hub_default_private",
            "bool",
            "publishing",
            "New Hub repositories are private unless you choose otherwise.",
            default="true",
        ),
    )
}

#: The two mask shapes core/encryption.mask_value produces.
_MASK_PATTERN = re.compile(r"^(\*\*\*|.{1,8}\.\.\..{1,8})$")


def is_masked_value(value: str) -> bool:
    """Does ``value`` look like a masked display string rather than a real secret?"""
    return bool(_MASK_PATTERN.fullmatch(value))


def parse_bool(raw: str | None, default: bool) -> bool:
    """Read a boolean setting. Absent or unparsable falls back to ``default``, NEVER to False.

    miStudio recorded the trap: for a ``dry_run`` setting, False means "delete".
    """
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"true", "1", "yes", "on"}:
        return True
    if value in {"false", "0", "no", "off"}:
        return False
    logger.warning("Boolean setting value %r is not a boolean; using the default %s", raw, default)
    return default


def get_spec(key: str) -> SettingSpec:
    spec = SETTINGS.get(key)
    if spec is None:
        raise NotFoundError(f"There is no setting called {key!r}.", code="UNKNOWN_SETTING")
    return spec


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


def _display(spec: SettingSpec, row: AppSetting | None) -> SettingView:
    if row is None:
        return SettingView(
            spec.key,
            spec.default,
            spec.type == "secret",
            False,
            spec.category,
            spec.description,
            spec.type,
        )
    value = row.value
    if row.is_sensitive:
        try:
            value = mask_value(decrypt_value(row.value, setting_key=row.key))
        except DecryptionError:
            value = "***"
    return SettingView(
        spec.key, value, row.is_sensitive, True, spec.category, spec.description, spec.type
    )


class AppSettingService:
    @staticmethod
    async def _row(db: AsyncSession, key: str) -> AppSetting | None:
        return (
            await db.execute(select(AppSetting).where(AppSetting.key == key))
        ).scalar_one_or_none()

    @staticmethod
    async def list_views(db: AsyncSession, *, for_agent: bool = False) -> list[SettingView]:
        rows = {r.key: r for r in (await db.execute(select(AppSetting))).scalars()}
        return [
            _display(spec, rows.get(key))
            for key, spec in sorted(SETTINGS.items())
            if not (for_agent and spec.agent_forbidden)
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
    async def get_bool(db: AsyncSession, key: str) -> bool:
        spec = get_spec(key)
        if spec.type != "bool" or spec.default is None:
            raise AppError(f"{key} is not a boolean setting.", code="NOT_BOOLEAN")
        return parse_bool(
            await AppSettingService.get_plain(db, key), parse_bool(spec.default, True)
        )

    @staticmethod
    async def upsert(db: AsyncSession, key: str, value: str) -> tuple[SettingView, bool]:
        """Write a setting. Returns ``(view, changed)``.

        A masked value sent back for a secret (the UI re-saving a form it was shown) leaves the
        stored ciphertext byte-identical and returns ``changed=False``.
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
        elif spec.type == "bool":
            normalised = value.strip().lower()
            if normalised not in {"true", "false"}:
                raise AppError(f"{key} takes true or false.", code="NOT_BOOLEAN", status_code=422)
            stored, sensitive = normalised, False
        else:
            stored, sensitive = value.strip(), False
        if row is None:
            row = AppSetting(key=key, value=stored, is_sensitive=sensitive, category=spec.category)
            db.add(row)
        else:
            row.value = stored
            row.is_sensitive = sensitive
            row.category = spec.category
        await db.commit()
        await db.refresh(row)
        return _display(spec, row), True

    @staticmethod
    async def delete(db: AsyncSession, key: str) -> bool:
        get_spec(key)
        row = await AppSettingService._row(db, key)
        if row is None:
            return False
        await db.delete(row)
        await db.commit()
        return True
