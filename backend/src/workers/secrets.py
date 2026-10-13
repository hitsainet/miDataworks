"""Stored secrets, decrypted only inside a worker (ADR-015; Foundation task 8.5).

The API never returns the Hugging Face token or an endpoint key: no route reads them in clear.
This module is the only place they are decrypted, and
``tests/unit/test_secret_isolation.py`` walks the abstract syntax tree of every module outside
``src/workers/`` and fails if one imports it. A per-import token is never stored at all; it
travels in a job's in-memory arguments only.

Every value decrypted here is registered with the log redactor by ``decrypt_value``.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.encryption import decrypt_value
from ..models.app_setting import AppSetting
from ..models.endpoint_role import EndpointRole


def resolve_hf_token(db: Session) -> str | None:
    """The stored Hugging Face token, or None when none is stored."""
    row = db.execute(select(AppSetting).where(AppSetting.key == "hf_token")).scalar_one_or_none()
    if row is None:
        return None
    return decrypt_value(row.value, setting_key="hf_token")


def resolve_endpoint_key(db: Session, role: str) -> str | None:
    """The stored API key of one endpoint role, or None."""
    row = db.get(EndpointRole, role)
    if row is None or row.api_key_ciphertext is None:
        return None
    return decrypt_value(row.api_key_ciphertext, setting_key=f"endpoint_role:{role}")
