# Origin: miStudio (Onegaishimas/miStudio) backend/src/api/v1/endpoints/settings.py @ c829a2cc
# Mode: adapt (docs/REUSE.md). Kept: masked responses only. Changed: keys are declared (unknown
# key 404), agents cannot read or write `operator_name` (C5), an agent's secret write waits for
# approval (`secret_write`, P-11), no PIN routes.
"""Settings routes (ADR-015; Foundation tasks 8.1, 8.4, 8.7)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.agent_origin import Actor, get_actor, requires_approval_when_agent
from ....core.database import get_db
from ....core.errors import ForbiddenError, NotFoundError
from ....schemas.settings import SettingOut, SettingWrite
from ....services.app_setting_service import AppSettingService, SettingView, get_spec

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


def _out(view: SettingView) -> SettingOut:
    return SettingOut(**view.__dict__)


def _refuse_agent_if_forbidden(key: str, actor: Actor) -> None:
    if actor.origin == "agent" and get_spec(key).agent_forbidden:
        raise ForbiddenError(
            f"Agents cannot read or change {key}; it records the operator's own identity.",
            code="AGENT_FORBIDDEN",
        )


def _is_secret_write(values: dict[str, object], _db: AsyncSession) -> bool:
    return get_spec(str(values["key"])).type == "secret"


@router.get("", response_model=list[SettingOut])
async def list_settings(
    db: AsyncSession = Depends(get_db), actor: Actor = Depends(get_actor)
) -> list[SettingOut]:
    """Every declared setting. Secrets are masked."""
    views = await AppSettingService.list_views(db, for_agent=actor.origin == "agent")
    return [_out(v) for v in views]


@router.get("/{key}", response_model=SettingOut)
async def get_setting(
    key: str, db: AsyncSession = Depends(get_db), actor: Actor = Depends(get_actor)
) -> SettingOut:
    _refuse_agent_if_forbidden(key, actor)
    return _out(await AppSettingService.get_view(db, key))


@router.put("/{key}", response_model=SettingOut)
@requires_approval_when_agent(
    "secret_write",
    when=_is_secret_write,
    secret_fields=("body.value",),
    summary=lambda p: f"Write the secret setting {p.get('key')}",
)
async def put_setting(
    key: str,
    body: SettingWrite,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> SettingOut:
    """Write a setting. A masked value sent back for a secret leaves the stored secret unchanged."""
    _refuse_agent_if_forbidden(key, actor)
    view, _changed = await AppSettingService.upsert(db, key, body.value)
    return _out(view)


@router.delete("/{key}", status_code=204)
@requires_approval_when_agent(
    "secret_write",
    when=_is_secret_write,
    summary=lambda p: f"Clear the secret setting {p.get('key')}",
)
async def delete_setting(
    key: str, db: AsyncSession = Depends(get_db), actor: Actor = Depends(get_actor)
) -> Response:
    _refuse_agent_if_forbidden(key, actor)
    if not await AppSettingService.delete(db, key):
        raise NotFoundError(f"{key} is not set.", code="SETTING_NOT_SET")
    return Response(status_code=204)
