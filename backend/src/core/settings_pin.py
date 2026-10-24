"""The Settings PIN (feature 014; operator directive 2026-10-20), modelled on miStudio's.

**Not a login.** The suite has no user accounts by decision (no-login-for-the-suite, 2026-10-06).
The PIN is a lock on the Settings writes, so a passer-by on the shared screen, or a script that
forgot which server it was pointed at, cannot change endpoints, keys or runtime settings.

- **Stored** as PBKDF2-SHA256 with a random salt (600,000 iterations) in ``dw_settings_pin``, a
  table no settings route reaches (miStudio's hash sat in its generic settings table, where
  ``DELETE /settings/{key}`` could remove the PIN without knowing it).
- **Unlock**: ``POST /api/v1/settings/pin/session`` with the PIN answers a session token, valid
  for :data:`SESSION_TTL_S`. Writes carry it in :data:`SESSION_HEADER`. The token is an HMAC over
  its expiry and the current PIN hash, keyed from ``SETTINGS_ENCRYPTION_KEY``: nothing is stored
  per session, every API process accepts it, and changing or removing the PIN ends every session.
- **Enforced on the server** by :func:`require_settings_pin`, a route dependency on every Settings
  write: ``403 PIN_REQUIRED`` without a valid token. A route dependency is not part of the
  approval executor's replay (``core/agent_origin.py``), so an approved agent request executes
  without the PIN: the operator's approval is the gate. An agent's request is passed on to the
  route's approval gate, which queues it (every PIN-gated route gates EVERY agent call).
- **Recovery**: ``SETTINGS_PIN_BYPASS=true`` in the backend's environment (needs access to the
  deployment, never settable from the app) lets every Settings write through and lets the PIN be
  set or removed without the current one. Remove it and restart afterwards.
- **Guessing** is slowed: after :data:`MAX_FAILURES` wrong PINs in a row this process refuses
  further tries for :data:`LOCKOUT_S` seconds (``429 PIN_LOCKED``), on top of PBKDF2's cost.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from .agent_origin import AGENT_HEADER
from .database import get_db
from .errors import AppError

SESSION_HEADER = "X-Settings-Pin-Session"
SESSION_TTL_S = 30 * 60
PBKDF2_ITERATIONS = 600_000
PIN_MIN_DIGITS = 4
PIN_MAX_DIGITS = 12
MAX_FAILURES = 5
LOCKOUT_S = 60.0


# --------------------------------------------------------------------------------------------
# Hashing
# --------------------------------------------------------------------------------------------


def check_format(pin: str) -> None:
    if not (pin.isdigit() and pin.isascii() and PIN_MIN_DIGITS <= len(pin) <= PIN_MAX_DIGITS):
        raise AppError(
            f"A Settings PIN is {PIN_MIN_DIGITS} to {PIN_MAX_DIGITS} digits.",
            code="PIN_FORMAT",
            status_code=422,
        )


def hash_pin(pin: str) -> str:
    """``pbkdf2_sha256$<iterations>$<salt b64>$<hash b64>``."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "$".join(
        (
            "pbkdf2_sha256",
            str(PBKDF2_ITERATIONS),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        )
    )


def verify_pin(pin: str, stored: str) -> bool:
    try:
        scheme, iterations, salt_b64, hash_b64 = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, int(iterations))
    except (ValueError, binascii.Error):
        return False
    return hmac.compare_digest(digest, expected)


# --------------------------------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------------------------------


def _session_key() -> bytes:
    from .config import env_settings

    material = env_settings().settings_encryption_key.get_secret_value().encode("utf-8")
    return hashlib.sha256(b"midataworks-settings-pin-session-v1\x00" + material).digest()


def _mac(expires: int, nonce: str, pin_hash: str) -> str:
    message = f"{expires}.{nonce}.{pin_hash}".encode()
    return hmac.new(_session_key(), message, hashlib.sha256).hexdigest()


def mint_session(pin_hash: str, now: float | None = None) -> tuple[str, int]:
    """A token for the PIN whose hash is ``pin_hash``, and its expiry (epoch seconds)."""
    expires = int((now if now is not None else time.time()) + SESSION_TTL_S)
    nonce = secrets.token_hex(8)
    return f"{expires}.{nonce}.{_mac(expires, nonce, pin_hash)}", expires


def session_valid(token: str | None, pin_hash: str, now: float | None = None) -> bool:
    if not token:
        return False
    try:
        expires_s, nonce, mac = token.split(".")
        expires = int(expires_s)
    except ValueError:
        return False
    if expires <= (now if now is not None else time.time()):
        return False
    return hmac.compare_digest(mac, _mac(expires, nonce, pin_hash))


# --------------------------------------------------------------------------------------------
# Guessing
# --------------------------------------------------------------------------------------------


@dataclass
class _Failures:
    count: int = 0
    locked_until: float = 0.0


_failures = _Failures()
_failures_lock = threading.Lock()


def check_not_locked(now: float | None = None) -> None:
    current = now if now is not None else time.monotonic()
    with _failures_lock:
        wait = _failures.locked_until - current
    if wait > 0:
        raise AppError(
            f"Too many wrong PINs. Try again in {int(wait) + 1} seconds.",
            code="PIN_LOCKED",
            status_code=429,
            details={"retry_after_s": int(wait) + 1},
        )


def record_attempt(ok: bool, now: float | None = None) -> None:
    current = now if now is not None else time.monotonic()
    with _failures_lock:
        if ok:
            _failures.count = 0
            _failures.locked_until = 0.0
            return
        _failures.count += 1
        if _failures.count >= MAX_FAILURES:
            _failures.count = 0
            _failures.locked_until = current + LOCKOUT_S


def reset_failures() -> None:
    """Tests: forget earlier wrong PINs."""
    with _failures_lock:
        _failures.count = 0
        _failures.locked_until = 0.0


# --------------------------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------------------------


def bypass_active() -> bool:
    """``SETTINGS_PIN_BYPASS``: read from the ENVIRONMENT only, never from an override."""
    from .config import env_settings

    return bool(env_settings().settings_pin_bypass)


async def stored_hash(db: AsyncSession) -> str | None:
    from ..models.settings_pin import SettingsPin

    row = await db.get(SettingsPin, 1)
    return row.pin_hash if row is not None else None


#: Approval actions that change Settings: approving one needs the PIN too, or anyone could send
#: a request as an "agent" and approve it themselves, which would make the PIN decoration.
SETTINGS_ACTIONS: frozenset[str] = frozenset({"config_write", "secret_write"})


async def check_pin(request: Request, db: AsyncSession) -> None:
    """``403 PIN_REQUIRED`` unless a valid PIN session is presented, no PIN is set, or the
    recovery bypass is on."""
    if bypass_active():
        return
    pin_hash = await stored_hash(db)
    if pin_hash is None:
        return
    token = request.headers.get(SESSION_HEADER)
    if session_valid(token, pin_hash):
        return
    raise AppError(
        (
            "Settings are locked with a PIN. Enter the Settings PIN to change them."
            if not token
            else "The Settings PIN session has ended or is not valid. Enter the Settings PIN again."
        ),
        code="PIN_REQUIRED",
        status_code=403,
        details={"header": SESSION_HEADER, "unlock": "/api/v1/settings/pin/session"},
    )


async def require_settings_pin(request: Request, db: AsyncSession = Depends(get_db)) -> None:
    """Route dependency on every Settings write (``dependencies=[SETTINGS_PIN]``). An agent
    request passes on to the route, whose approval gate queues it for the operator, or which
    refuses agents outright: it never executes here without approval."""
    if request.headers.get(AGENT_HEADER) is not None:
        return
    await check_pin(request, db)


#: ``@router.put(..., dependencies=[SETTINGS_PIN])``. A route dependency, not a parameter, so the
#: approval executor never replays it: an approved request runs without the PIN.
SETTINGS_PIN = Depends(require_settings_pin)
