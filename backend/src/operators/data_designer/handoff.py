"""Hand the model endpoint's API key to the designer worker without putting it in a message
(FR-003.15; ADR-015; PADR ADR-010 amendment 2026-10-07).

A Celery message sits in Redis, readable by anything with the broker URL, so the key never travels
in one. The backend seals it with AES-256-GCM under a key derived from the shared
``DESIGNER_HANDOFF_KEY`` (a Kubernetes Secret held by the backend and the designer pod only — not
the settings encryption key, which the designer pod never sees), stores it under
``dw:designer:key:<ref>`` with a lifetime and ``NX``, and passes only ``<ref>``. The designer worker
takes it with ``GETDEL`` (``runner.take_key``), so it exists in one place for as long as a step needs
it. Byte-compatible with ``runner.open_sealed``; ``test_designer_handoff.py`` proves it.
"""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ...core.config import get_settings
from ...core.logging import register_secret
from ..errors import OperatorError

PREFIX = "dw:designer:key:"
#: A step may wait in the designer queue; the key must outlive the wait but not the day.
TTL_S = 6 * 3600


def _secret() -> str:
    configured = get_settings().designer_handoff_key
    value = configured.get_secret_value() if configured is not None else ""
    if not value:
        raise OperatorError(
            "worker_unavailable",
            "DESIGNER_HANDOFF_KEY is not set, so a model key cannot be handed to the Data Designer "
            "worker. Add it to the midataworks-secrets Secret (see k8s/base/designer.yaml).",
        )
    return value


def seal(ref: str, api_key: str, secret: str) -> bytes:
    aead = AESGCM(hashlib.sha256(secret.encode("utf-8")).digest())
    nonce = os.urandom(12)
    return base64.b64encode(nonce + aead.encrypt(nonce, api_key.encode("utf-8"), ref.encode()))


def put(ref: str, api_key: str) -> None:
    """Store the sealed key under ``ref`` for the designer worker to take once."""
    import redis

    register_secret(api_key)
    sealed = seal(ref, api_key, _secret())
    client = redis.Redis.from_url(get_settings().redis_url)
    if not client.set(PREFIX + ref, sealed, ex=TTL_S, nx=True):
        raise OperatorError("worker_unavailable", f"A key is already waiting under {ref}.")
