# Origin: miStudio (Onegaishimas/miStudio) backend/src/api/v1/endpoints/settings.py @ c829a2cc
# Mode: adapt (docs/REUSE.md). Kept: masked responses only. Changed: keys are declared (unknown
# key 404), agents cannot read or write `operator_name` (C5), an agent's secret write waits for
# approval (`secret_write`, P-11). Writes need the Settings PIN when one is set (feature 014;
# the PIN routes are in settings_pin.py). Feature 012 (ENH-003): the runtime environment
# settings — the Environment listing, typed and validated writes with an optional reason, history,
# `config_write` approval for agents, and a refresh of this process's overlay after every write.
"""Settings routes (ADR-015; Foundation tasks 8.1, 8.4, 8.7; feature 012)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ....core import runtime_settings
from ....core.agent_origin import Actor, get_actor, requires_approval_when_agent
from ....core.database import get_db
from ....core.errors import AppError, ForbiddenError, NotFoundError
from ....core.settings_pin import SETTINGS_PIN
from ....models.setting_change import REASON_MAX_CHARS
from ....schemas.settings import EnvironmentOut, SettingOut, SettingWrite
from ....services import environment_service
from ....services.app_setting_service import AppSettingService, SettingView, get_spec

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


def _out(view: SettingView) -> SettingOut:
    return SettingOut(**view.__dict__)


def _refuse_agent_if_forbidden(key: str, actor: Actor) -> None:
    if actor.origin != "agent":
        return
    spec = get_spec(key)
    if not spec.agent_forbidden:
        return
    if spec.runtime:
        raise ForbiddenError(
            f"Agents cannot change {key}; only the operator can, on the Settings screen.",
            code="AGENT_FORBIDDEN",
        )
    raise ForbiddenError(
        f"Agents cannot read or change {key}; it records the operator's own identity.",
        code="AGENT_FORBIDDEN",
    )


def _is_secret(key: str) -> bool:
    return get_spec(key).type == "secret"


def _action_for(values: dict[str, Any]) -> str:
    return "secret_write" if _is_secret(str(values["key"])) else "config_write"


def _before_agent_write(values: dict[str, Any], actor: Actor) -> None:
    """Refuse an agent write that could never run, before anything is queued (012 FTID 7.3)."""
    key = str(values["key"])
    _refuse_agent_if_forbidden(key, actor)  # also 404/422 for unknown or non-runtime keys
    body = values.get("body")
    spec = get_spec(key)
    if spec.runtime and isinstance(body, SettingWrite):
        AppSettingService.validate_runtime(key, body.value, runtime_settings.overrides())


def _before_agent_delete(values: dict[str, Any], actor: Actor) -> None:
    _refuse_agent_if_forbidden(str(values["key"]), actor)


def _check_reason(reason: str | None) -> str | None:
    if reason is not None and len(reason) > REASON_MAX_CHARS:
        raise AppError(
            f"A reason is at most {REASON_MAX_CHARS} characters.",
            code="REASON_TOO_LONG",
            status_code=422,
        )
    return reason


@router.get("", response_model=list[SettingOut])
async def list_settings(
    db: AsyncSession = Depends(get_db), actor: Actor = Depends(get_actor)
) -> list[SettingOut]:
    """Every declared setting. Secrets are masked."""
    views = await AppSettingService.list_views(db, for_agent=actor.origin == "agent")
    return [_out(v) for v in views]


# Declared before "/{key}" so "environment" is never read as a setting's name.
@router.get("/environment", response_model=EnvironmentOut)
async def get_environment(
    db: AsyncSession = Depends(get_db), actor: Actor = Depends(get_actor)
) -> EnvironmentOut:
    """Every environment setting, grouped: changeable while miDataworks runs, needs a restart,
    approval rules (deployment configuration), and the rest from the environment. Secrets are
    shown as set or not set, and passwords are removed from addresses. With the last 50 changes."""
    return await environment_service.listing(db, for_agent=actor.origin == "agent")


@router.get("/{key}", response_model=SettingOut)
async def get_setting(
    key: str, db: AsyncSession = Depends(get_db), actor: Actor = Depends(get_actor)
) -> SettingOut:
    _refuse_agent_if_forbidden(key, actor)
    return _out(await AppSettingService.get_view(db, key))


@router.put("/{key}", response_model=SettingOut, dependencies=[SETTINGS_PIN])
@requires_approval_when_agent(
    "secret_write",
    actions=("config_write",),
    action_for=_action_for,
    before=_before_agent_write,
    secret_fields=("body.value",),
    summary=lambda p: (
        f"Change the setting {p.get('key')} to {(p.get('body') or {}).get('value')!r}"
        if not _is_secret(str(p.get("key")))
        else f"Write the secret setting {p.get('key')}"
    ),
)
async def put_setting(
    key: str,
    body: SettingWrite,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> SettingOut:
    """Write a setting. A masked value sent back for a secret leaves the stored secret unchanged.

    An agent's write waits for the operator: ``secret_write`` for a secret, ``config_write`` for a
    runtime environment setting (feature 012). Operator-only keys are refused to agents."""
    _refuse_agent_if_forbidden(key, actor)
    author = await AppSettingService.author(db, actor.origin, actor.agent, actor.approval_id)
    view, _changed = await AppSettingService.upsert(
        db, key, body.value, author=author, reason=_check_reason(body.reason)
    )
    if get_spec(key).runtime:
        await runtime_settings.refresh_async(db)  # this process sees its own change at once
    return _out(view)


@router.delete("/{key}", status_code=204, dependencies=[SETTINGS_PIN])
@requires_approval_when_agent(
    "secret_write",
    actions=("config_write",),
    action_for=_action_for,
    before=_before_agent_delete,
    summary=lambda p: (
        f"Return the setting {p.get('key')} to its environment value"
        if not _is_secret(str(p.get("key")))
        else f"Clear the secret setting {p.get('key')}"
    ),
)
async def delete_setting(
    key: str,
    reason: str | None = Query(None, max_length=REASON_MAX_CHARS),
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> Response:
    """Clear a stored setting. For a runtime setting this is "use the environment value"."""
    _refuse_agent_if_forbidden(key, actor)
    author = await AppSettingService.author(db, actor.origin, actor.agent, actor.approval_id)
    if not await AppSettingService.delete(db, key, author=author, reason=_check_reason(reason)):
        raise NotFoundError(f"{key} is not set.", code="SETTING_NOT_SET")
    if get_spec(key).runtime:
        await runtime_settings.refresh_async(db)
    return Response(status_code=204)
