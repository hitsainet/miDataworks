"""The ephemeral secret store: a per-import token, in transit only (FR-001.12; 001 FTASKS 3.3;
T-05).

``put`` writes ``dw:eph:<key>`` to Redis, AES-256-GCM-encrypted under a key derived for this purpose
alone (HKDF info ``dw-ephemeral-secret``), with ``EX`` (a lifetime) and ``NX`` (a second put on the
same key is refused). ``take`` is ``GETDEL``: the first reader gets the token and the store forgets
it, so a token exists in exactly one place for as long as a job needs it.

Only the worker calls ``take`` (``tests/unit/test_token_import_boundaries.py``). Nothing here logs,
and both the value put and the value taken are registered with the log redactor (the
redactor is value-based: 001 FTASKS 12.6's "field name" maps to registering the value).
"""

from __future__ import annotations

import base64
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import get_settings
from .encryption import derive_subkey
from .logging import register_secret

PREFIX = "dw:eph:"
_INFO = b"dw-ephemeral-secret"

_client: Any = None


class EphemeralKeyTaken(RuntimeError):
    """A second ``put`` on a key that already holds a secret."""


def redis_client() -> Any:
    global _client
    if _client is None:
        import redis

        _client = redis.Redis.from_url(get_settings().redis_url)
    return _client


def _aead() -> AESGCM:
    return AESGCM(derive_subkey(_INFO))


def put(key: str, secret: str, ttl_s: int | None = None) -> None:
    """Store ``secret`` under ``key`` for ``ttl_s`` seconds; refuse if the key is taken."""
    register_secret(secret)  # the API holds the plain value now: no log line may carry it
    nonce = os.urandom(12)
    sealed = base64.b64encode(nonce + _aead().encrypt(nonce, secret.encode("utf-8"), key.encode()))
    ttl = ttl_s if ttl_s is not None else get_settings().ephemeral_secret_ttl_s
    if not redis_client().set(PREFIX + key, sealed, ex=ttl, nx=True):
        raise EphemeralKeyTaken(f"an ephemeral secret is already stored under {key}")


def take(key: str) -> str | None:
    """Read and delete in one step (``GETDEL``); None when absent or expired."""
    raw = redis_client().getdel(PREFIX + key)
    if raw is None:
        return None
    blob = base64.b64decode(raw)
    value = _aead().decrypt(blob[:12], blob[12:], key.encode()).decode("utf-8")
    register_secret(value)
    return value
