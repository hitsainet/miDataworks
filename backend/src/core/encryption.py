# Origin: miStudio (Onegaishimas/miStudio) backend/src/core/encryption.py @ c829a2cc
# Mode: copy, then adapt (docs/REUSE.md). Changes from the origin:
#   - the key comes only from SETTINGS_ENCRYPTION_KEY (required by core/config.py); there is no
#     fallback to a secret key, so a missing key fails at start instead of encrypting with
#     something nobody backed up;
#   - the HKDF salt is miDataworks' own, so a ciphertext cannot be moved between the two apps;
#   - every decrypted value is registered with core/logging.register_secret, so it can never be
#     logged afterwards (ADR-015);
#   - type annotations for MyPy strict.
"""
AES-256-GCM envelope encryption for sensitive application settings.

Provides encrypt/decrypt functions and masked display for API keys and secrets.
Uses HKDF key derivation from SETTINGS_ENCRYPTION_KEY or falls back to SECRET_KEY.

Security properties:
- AES-256-GCM: Authenticated encryption (confidentiality + integrity)
- 96-bit random nonce per encryption (never reused)
- HKDF-SHA256 key derivation ensures proper key material
- Post-quantum resistant for at-rest encryption (256-bit symmetric)
"""

import base64
import binascii
import logging
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .logging import register_secret

logger = logging.getLogger(__name__)


class DecryptionError(Exception):
    """A stored value is a well-formed AES-GCM envelope that failed to authenticate.

    Distinct from "this row was never encrypted", which is legacy data and is
    returned as-is. This one means the ciphertext cannot be trusted.
    """


# Legacy plaintext rows observed this process. Counted rather than merely logged
# so the exposure is measurable — MIS-E2E-041 persisted because the path was
# silent and nothing could report on it.
_LEGACY_PLAINTEXT_READS: list[str] = []


def legacy_plaintext_reads() -> list[str]:
    """Setting keys read as plaintext since process start. Empty is the goal."""
    return list(_LEGACY_PLAINTEXT_READS)


# Module-level derived key (initialized on first use)
_derived_key: bytes | None = None


def _get_encryption_key() -> bytes:
    """Derive a 256-bit AES key from the configured key material using HKDF."""
    global _derived_key
    if _derived_key is not None:
        return _derived_key

    from .config import get_settings

    # No fallback: SETTINGS_ENCRYPTION_KEY is required (core/config.py).
    key_material = get_settings().settings_encryption_key.get_secret_value()

    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,  # 256 bits
        salt=b"midataworks-settings-v1",
        info=b"settings-encryption",
    )
    _derived_key = hkdf.derive(key_material.encode("utf-8"))
    return _derived_key


def encrypt_value(plaintext: str) -> str:
    """Encrypt a plaintext string using AES-256-GCM.

    Returns a base64-encoded string containing: nonce (12 bytes) + ciphertext + tag (16 bytes).
    """
    key = _get_encryption_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)  # 96-bit nonce
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    # Concatenate nonce + ciphertext+tag, then base64 encode
    return base64.b64encode(nonce + ciphertext).decode("ascii")


def decrypt_value(encrypted: str, *, setting_key: str | None = None) -> str:
    """Decrypt a base64-encoded AES-256-GCM ciphertext.

    Expects the format produced by encrypt_value(): base64(nonce + ciphertext + tag).

    TWO FAILURE MODES, DELIBERATELY TREATED DIFFERENTLY (MIS-E2E-004/041/056).

    * **Not an envelope** — bad base64, or too short to hold nonce+tag. That is a
      legacy row written as plaintext before service-layer encryption existed.
      Returned as-is, because it IS the value, and counted so the exposure is
      measurable instead of invisible.

    * **InvalidTag** — the bytes decode as an envelope and fail authentication.
      That is tampering, or a key that has changed. **Raises.**

    This used to swallow both. The consequence was not abstract: after a key
    change, `decrypt_value` handed back raw base64 ciphertext, which
    `OpenAILabelingService` then sent to api.openai.com in an
    `Authorization: Bearer` header. Encrypted credential material left the
    network boundary because a decryption failure was designed to be silent.
    Swallowing an authentication failure also discards the integrity half of
    AES-GCM, which is the reason the mode was chosen.

    Raises:
        DecryptionError: the value is a well-formed envelope that failed
            authentication. Do not treat the return value as a credential.

    Args:
        encrypted: The stored value (expected to be base64 ciphertext).
        setting_key: Optional name of the setting being decrypted, used to
            identify the row in the warning log. Pass it whenever available.
    """
    key = _get_encryption_key()
    try:
        raw = base64.b64decode(encrypted, validate=True)
        if len(raw) < 12 + 16:  # nonce + GCM tag minimum
            raise ValueError(
                f"ciphertext too short for AES-GCM envelope " f"(got {len(raw)} bytes, need ≥28)"
            )
        nonce = raw[:12]
        ciphertext = raw[12:]
        aesgcm = AESGCM(key)
        plaintext = aesgcm.decrypt(nonce, ciphertext, None).decode("utf-8")
        register_secret(plaintext)
        return plaintext
    except InvalidTag as exc:
        # AUTHENTICATED ENCRYPTION FAILED. The bytes are a well-formed envelope
        # and the tag does not match: the row was tampered with, or the key
        # changed. Returning the ciphertext here is what sent credential
        # material to a third party (MIS-E2E-056), so refuse.
        which = f"setting {setting_key!r}" if setting_key else "a settings row"
        logger.error(
            "decrypt_value: AUTHENTICATION FAILED for %s. The stored value is a "
            "well-formed AES-GCM envelope whose tag does not verify — most often "
            "SETTINGS_ENCRYPTION_KEY (or the fallback SECRET_KEY) changed since "
            "the row was written, otherwise the row was modified. Refusing to "
            "return the ciphertext: re-enter the value via Settings.",
            which,
        )
        raise DecryptionError(
            f"could not authenticate {which} — the encryption key has changed "
            f"or the stored value was modified"
        ) from exc

    except Exception as exc:
        # NOT AN ENVELOPE. A legacy row written as plaintext before the
        # service-layer encryption existed. That is the case this fallback was
        # built for, and it is the only one it still covers.
        if isinstance(exc, binascii.Error):
            cause = "value is not valid base64 — was stored as plaintext"
        elif isinstance(exc, ValueError) and "too short" in str(exc):
            cause = str(exc) + " — likely plaintext or truncated"
        else:
            cause = f"{type(exc).__name__}: {exc}"

        which = f"setting {setting_key!r}" if setting_key else "a settings row"
        # COUNTED, not just logged. MIS-E2E-041: legacy plaintext credentials can
        # persist indefinitely precisely because this path is silent — nothing
        # could ever notice them, so no backfill was ever prompted.
        _LEGACY_PLAINTEXT_READS.append(setting_key or "<unknown>")
        register_secret(encrypted)
        logger.warning(
            "decrypt_value: %s is stored as PLAINTEXT (%s). Returning as-is for "
            "backward compatibility. This value is NOT encrypted at rest — "
            "re-enter and save it via Settings. "
            "(legacy plaintext reads this process: %d)",
            which,
            cause,
            len(_LEGACY_PLAINTEXT_READS),
        )
        return encrypted


def mask_value(plaintext: str, visible_prefix: int = 3, visible_suffix: int = 4) -> str:
    """Create a masked display string for a sensitive value.

    Examples:
        "sk-proj-abc123xyz789" -> "sk-...789"
        "hf_abcdefghijk"       -> "hf_...hijk"
        "abcd"                 -> "***"       (too short to mask meaningfully)

    MIS-E2E-061: the old branch returned
    `plaintext[:3] + "..." + plaintext[-4:]` for any value longer than 3
    characters, so a 4-to-7-character secret was emitted IN FULL —
    `"abcd"` masked to `"abc...abcd"`, and the docstring's own
    `"short" -> "sho...ort"` example showed every character of the input.

    A value too short to leave a hidden middle is fully masked instead. This is
    the function the UI trusts not to show a credential, so the safe direction
    on an edge case is to reveal nothing.
    """
    # Need at least one hidden character between the two visible windows,
    # otherwise the "mask" is the value.
    if len(plaintext) <= visible_prefix + visible_suffix:
        return "***"
    return plaintext[:visible_prefix] + "..." + plaintext[-visible_suffix:]


def reset_key_cache() -> None:
    """Reset the cached derived key. Useful for testing."""
    global _derived_key
    _derived_key = None


def derive_subkey(info: bytes) -> bytes:
    """A separate 256-bit key from the same material, for a different purpose (HMAC digests).

    Distinct ``info`` strings give independent keys, so a digest key never doubles as the
    encryption key (010 FTDD section 4.4).
    """
    from .config import get_settings

    key_material = get_settings().settings_encryption_key.get_secret_value()
    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=b"midataworks-settings-v1", info=info
    ).derive(key_material.encode("utf-8"))
