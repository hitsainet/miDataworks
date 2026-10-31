"""The Settings PIN routes (feature 014; operator directive 2026-10-20). See ``core/settings_pin.py``.

Operator only: an agent is answered ``403 AGENT_FORBIDDEN`` on every write here, and no MCP tool
reaches them (``tests/support/mcp_ledger.py``, ``ROUTE_EXEMPT``). Setting, changing and removing the
PIN need the current PIN (unless ``SETTINGS_PIN_BYPASS`` is on). The history records that the PIN
was set, changed or removed, never the PIN or its hash.

Registered BEFORE the settings router (``api/v1/router.py``), so ``/settings/pin`` is never read
as the name of a setting.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ....core import settings_pin as pin
from ....core.agent_origin import Actor, get_actor
from ....core.database import get_db
from ....core.errors import AppError, ForbiddenError
from ....models.settings_pin import SettingsPin
from ....services.app_setting_service import AppSettingService

router = APIRouter(prefix="/api/v1/settings/pin", tags=["settings"])

HISTORY_KEY = "settings_pin"


class PinStatusOut(BaseModel):
    configured: bool
    bypass_active: bool = Field(description="SETTINGS_PIN_BYPASS is on: Settings is unlocked")
    session_valid: bool = Field(description="This request carried a valid PIN session")
    session_ttl_s: int
    header: str = pin.SESSION_HEADER


class PinSessionIn(BaseModel):
    pin: str = Field(max_length=64)


class PinSessionOut(BaseModel):
    token: str
    expires_at: datetime
    header: str = pin.SESSION_HEADER


class PinSetIn(BaseModel):
    pin: str = Field(max_length=64)
    current_pin: str | None = Field(None, max_length=64)


class PinRemoveIn(BaseModel):
    current_pin: str | None = Field(None, max_length=64)


def _refuse_agent(actor: Actor) -> None:
    if actor.origin == "agent":
        raise ForbiddenError(
            "Agents cannot use or change the Settings PIN; only the operator can.",
            code="AGENT_FORBIDDEN",
        )


def _check_current(current: str | None, stored: str) -> None:
    """The current PIN, unless the recovery bypass is on. Wrong tries count towards the lockout."""
    if pin.bypass_active():
        return
    pin.check_not_locked()
    if not current:
        raise AppError(
            "Enter the current Settings PIN to change or remove it.",
            code="PIN_CURRENT_REQUIRED",
            status_code=403,
        )
    ok = pin.verify_pin(current, stored)
    pin.record_attempt(ok)
    if not ok:
        raise AppError("The current Settings PIN is wrong.", code="PIN_WRONG", status_code=403)


def _session(pin_hash: str) -> PinSessionOut:
    token, expires = pin.mint_session(pin_hash)
    return PinSessionOut(token=token, expires_at=datetime.fromtimestamp(expires, tz=UTC))


async def _record(db: AsyncSession, actor: Actor, previous: str | None, new: str | None) -> None:
    author = await AppSettingService.author(db, actor.origin, actor.agent, actor.approval_id)
    AppSettingService._record(db, HISTORY_KEY, previous, new, author, None)


@router.get("", response_model=PinStatusOut)
async def pin_status(request: Request, db: AsyncSession = Depends(get_db)) -> PinStatusOut:
    """Whether a PIN is set, whether the recovery bypass is on, and whether this request's
    session is valid. Never the hash."""
    stored = await pin.stored_hash(db)
    return PinStatusOut(
        configured=stored is not None,
        bypass_active=pin.bypass_active(),
        session_valid=stored is not None
        and pin.session_valid(request.headers.get(pin.SESSION_HEADER), stored),
        session_ttl_s=pin.SESSION_TTL_S,
    )


@router.post("/session", response_model=PinSessionOut)
async def unlock(
    body: PinSessionIn, db: AsyncSession = Depends(get_db), actor: Actor = Depends(get_actor)
) -> PinSessionOut:
    """Unlock Settings: the PIN in, a session token out (sent back in ``X-Settings-Pin-Session``)."""
    _refuse_agent(actor)
    stored = await pin.stored_hash(db)
    if stored is None:
        raise AppError("No Settings PIN is set.", code="PIN_NOT_SET", status_code=409)
    pin.check_not_locked()
    ok = pin.verify_pin(body.pin, stored)
    pin.record_attempt(ok)
    if not ok:
        raise AppError("That Settings PIN is wrong.", code="PIN_WRONG", status_code=403)
    return _session(stored)


@router.put("", response_model=PinSessionOut)
async def set_pin(
    body: PinSetIn, db: AsyncSession = Depends(get_db), actor: Actor = Depends(get_actor)
) -> PinSessionOut:
    """Set the PIN, or change it with the current one. Answers a session for the new PIN; every
    session of the old PIN ends."""
    _refuse_agent(actor)
    pin.check_format(body.pin)
    row = await db.get(SettingsPin, 1)
    if row is not None:
        _check_current(body.current_pin, row.pin_hash)
        row.pin_hash = pin.hash_pin(body.pin)
        await _record(db, actor, "set", "changed")
    else:
        row = SettingsPin(id=1, pin_hash=pin.hash_pin(body.pin))
        db.add(row)
        await _record(db, actor, None, "set")
    new_hash = row.pin_hash
    await db.commit()
    return _session(new_hash)


@router.post("/remove", response_model=PinStatusOut)
async def remove_pin(
    body: PinRemoveIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> PinStatusOut:
    """Remove the PIN with the current one: Settings is open again."""
    _refuse_agent(actor)
    row = await db.get(SettingsPin, 1)
    if row is None:
        raise AppError("No Settings PIN is set.", code="PIN_NOT_SET", status_code=409)
    _check_current(body.current_pin, row.pin_hash)
    await db.delete(row)
    await _record(db, actor, "set", None)
    await db.commit()
    return await pin_status(request, db)
