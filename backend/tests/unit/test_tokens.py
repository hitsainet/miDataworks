"""Token normalisation and the tier choice (001 FTASKS 3.1, 3.2; FR-001.11, FR-001.12).

Each absent form (None, empty, whitespace, ``none`` in any case) is absent at EVERY tier; per-import
beats stored; stored beats none; and a token the request said it supplied that is gone by the time
the worker looks fails ``token_expired`` and never falls back to the stored token (FTID IQ6).
"""

from __future__ import annotations

import pytest

from src.core.errors import AppError
from src.models.source_enums import TokenTier
from src.services.sources.tokens import choose_token, normalise_token

ABSENT = [None, "", "   ", "none", "None", "NONE", "  none  "]


@pytest.mark.parametrize("value", ABSENT)
def test_absent_forms_normalise_to_none(value: str | None) -> None:
    assert normalise_token(value) is None


def test_a_token_is_stripped_not_altered() -> None:
    assert normalise_token("  hf_AbC123  ") == "hf_AbC123"


@pytest.mark.parametrize("absent", ABSENT)
def test_an_absent_per_import_value_falls_through_to_the_stored_token(absent: str | None) -> None:
    assert choose_token(take=lambda: absent, stored=lambda: "hf_stored", supplied=False) == (
        "hf_stored",
        TokenTier.STORED,
    )


@pytest.mark.parametrize("absent", ABSENT)
def test_an_absent_stored_value_is_no_token(absent: str | None) -> None:
    assert choose_token(take=lambda: None, stored=lambda: absent, supplied=False) == (
        None,
        TokenTier.NONE,
    )


def test_per_import_beats_stored() -> None:
    assert choose_token(take=lambda: "hf_one", stored=lambda: "hf_two", supplied=True) == (
        "hf_one",
        TokenTier.PER_IMPORT,
    )


def test_the_stored_token_is_not_read_when_a_per_import_token_exists() -> None:
    def stored() -> str:
        raise AssertionError("the stored token must not be decrypted when not needed")

    assert choose_token(take=lambda: "hf_one", stored=stored, supplied=True)[1] is (
        TokenTier.PER_IMPORT
    )


@pytest.mark.parametrize("gone", [None, "", "none"])
def test_a_supplied_but_expired_token_fails_and_never_falls_back(gone: str | None) -> None:
    consulted: list[str] = []

    def stored() -> str:
        consulted.append("stored")
        return "hf_stored"

    with pytest.raises(AppError) as info:
        choose_token(take=lambda: gone, stored=stored, supplied=True)
    assert info.value.code == "token_expired"
    assert "enter the token" in info.value.message
    assert consulted == [], "an expired per-import token must never be replaced by the stored one"
