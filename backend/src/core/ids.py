"""Prefixed random identifiers (``job_…``, ``apr_…``) for rows the API exposes."""

from __future__ import annotations

import secrets


def new_id(prefix: str) -> str:
    """``<prefix>_<24 hex chars>``: 96 random bits, URL- and path-segment-safe."""
    return f"{prefix}_{secrets.token_hex(12)}"
