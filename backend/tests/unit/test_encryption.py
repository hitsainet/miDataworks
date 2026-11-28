"""AES-256-GCM settings encryption (ADR-015; Foundation task 8.1)."""

from __future__ import annotations

import base64

import pytest

from src.core.encryption import (
    DecryptionError,
    decrypt_value,
    derive_subkey,
    encrypt_value,
    mask_value,
    reset_key_cache,
)


def test_round_trip_and_fresh_nonce() -> None:
    a, b = encrypt_value("hf_secret_token_value"), encrypt_value("hf_secret_token_value")
    assert a != b, "each encryption must use a fresh nonce"
    assert decrypt_value(a) == decrypt_value(b) == "hf_secret_token_value"
    assert "hf_secret" not in a


def test_a_tampered_envelope_raises_instead_of_returning_ciphertext() -> None:
    raw = bytearray(base64.b64decode(encrypt_value("sk-live-key-123456")))
    raw[-1] ^= 0x01
    with pytest.raises(DecryptionError):
        decrypt_value(base64.b64encode(bytes(raw)).decode())


def test_a_changed_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import SecretStr

    from src.core.config import get_settings

    stored = encrypt_value("sk-live-key-123456")
    monkeypatch.setattr(
        get_settings(), "settings_encryption_key", SecretStr("another-key-" + "x" * 30)
    )
    reset_key_cache()
    try:
        with pytest.raises(DecryptionError):
            decrypt_value(stored)
    finally:
        monkeypatch.undo()
        reset_key_cache()


@pytest.mark.parametrize(
    ("value", "masked"),
    [("sk-proj-abc123xyz789", "sk-...z789"), ("abcd", "***"), ("abcdefg", "***")],
)
def test_mask_never_reveals_a_short_secret(value: str, masked: str) -> None:
    assert mask_value(value) == masked


def test_subkeys_are_distinct_from_each_other() -> None:
    assert derive_subkey(b"a") != derive_subkey(b"b")
    assert len(derive_subkey(b"a")) == 32


def test_a_decrypted_secret_is_registered_with_the_log_redactor(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Control E07 survived without this: decrypt_value must register what it decrypts, so a
    worker that logs a decrypted credential by mistake still logs nothing of it."""
    import logging

    secret = "hf_DecryptedOnlyInThisTest_7f3a9c21"
    envelope = encrypt_value(secret)
    with caplog.at_level(logging.INFO):
        logging.getLogger("before").info("before decrypt: %s", secret)
    assert secret in caplog.text, "precondition: an unregistered value is logged as-is"
    caplog.clear()
    assert decrypt_value(envelope) == secret
    with caplog.at_level(logging.INFO):
        logging.getLogger("after").info("after decrypt: %s", secret)
    assert secret not in caplog.text
