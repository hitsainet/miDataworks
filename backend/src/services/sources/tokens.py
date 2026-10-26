"""Token normalisation and the tier choice (FR-001.11, FR-001.12; 001 FTID section 3.4).

Origin of ``normalise_token``: miStudio (Onegaishimas/miStudio)
backend/src/services/huggingface_sae_service.py ``resolve_hf_token`` and its ``.lower() == "none"``
guard @ c829a2cc (pattern; the function is rewritten here).

Precedence: a per-import token (taken once from the ephemeral store), then the stored token from
Settings, then none. A job told a token was supplied that finds none fails ``token_expired``; it
never falls back silently to the stored token (FTID IQ6).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...core.errors import AppError
from ...models.source_enums import TokenTier


def normalise_token(value: Any) -> str | None:
    """None for None, empty, whitespace or the string ``none`` (any case); else the stripped token."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "none":
        return None
    return text


def choose_token(
    *,
    take: Callable[[], str | None],
    stored: Callable[[], str | None],
    supplied: bool,
) -> tuple[str | None, TokenTier]:
    """The token and its tier. ``take`` and ``stored`` are the worker's two sources."""
    per_import = normalise_token(take())
    if per_import:
        return per_import, TokenTier.PER_IMPORT
    if supplied:
        raise AppError(
            "The access token entered for this import has expired before the job started. Start "
            "the import again and enter the token.",
            code="token_expired",
            status_code=409,
        )
    from_settings = normalise_token(stored())
    if from_settings:
        return from_settings, TokenTier.STORED
    return None, TokenTier.NONE
