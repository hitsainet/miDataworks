"""Structured logging to standard output with secret redaction (ADR-015; Foundation task 3.6).

What this module guarantees: once a value is registered with :func:`register_secret`, no log
record created afterwards carries it — not in the message, not in the arguments, not in a
formatted traceback. Redaction happens when the record is CREATED (a log-record factory), not in
one handler's formatter, so it holds for every handler, including pytest's ``caplog`` and any
handler a library adds later. A filter on one handler would leave every other handler unguarded.

Secrets are registered by the code that holds them: configuration at start-up, and
:func:`src.core.encryption.decrypt_value` every time it decrypts a stored secret.
"""

from __future__ import annotations

import logging
import re
import sys
import threading
import traceback
from typing import Any

REDACTED = "[REDACTED]"

#: Values shorter than this are not registered: redacting "a" would shred every log line, and
#: no real credential is that short.
MIN_SECRET_LENGTH = 6

#: Second line of defence by FIELD NAME (001 FTDD 5.3, FTASKS 12.6): a value logged under one of
#: these names is redacted even if nobody registered it (a token typed into a request body).
SECRET_FIELDS = ("access_token", "api_key", "hf_token", "authorization")
_FIELD = re.compile(
    r"(?i)(\b(?:"
    + "|".join(SECRET_FIELDS)
    + r")\b['\"]?\s*[:=]\s*['\"]?(?:bearer\s+)?)((?!\[REDACTED\])[^\s'\",}\]()]+)"
)

_lock = threading.Lock()
_secrets: set[str] = set()
_installed = False
_base_factory = logging.getLogRecordFactory()


def register_secret(value: str | None) -> None:
    """Never log ``value`` again in this process."""
    if value and len(value) >= MIN_SECRET_LENGTH:
        with _lock:
            _secrets.add(value)
    install_redaction()


def redact(text: str) -> str:
    """Replace every registered secret in ``text``."""
    with _lock:
        secrets = sorted(_secrets, key=len, reverse=True)
    for secret in secrets:
        if secret in text:
            text = text.replace(secret, REDACTED)
    return _FIELD.sub(lambda m: m.group(1) + REDACTED, text)


def _contains_secret(text: str) -> bool:
    with _lock:
        if any(secret in text for secret in _secrets):
            return True
    return _FIELD.search(text) is not None


def _redacting_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
    record = _base_factory(*args, **kwargs)
    try:
        message = record.getMessage()
    except Exception:  # noqa: BLE001 - a malformed record must still be logged
        message = str(record.msg)
    if _contains_secret(message):
        record.msg = redact(message)
        record.args = None
    if record.exc_info and record.exc_info[0] is not None:
        formatted = "".join(traceback.format_exception(*record.exc_info))
        if _contains_secret(formatted):
            # Formatter.format uses exc_text when set, so the raw traceback is never rendered.
            record.exc_text = redact(formatted).rstrip("\n")
    return record


def install_redaction() -> None:
    """Install the redacting record factory once per process."""
    global _installed
    with _lock:
        if _installed:
            return
        logging.setLogRecordFactory(_redacting_factory)
        _installed = True


def configure_logging(level: str = "INFO") -> None:
    """Structured text to standard output, with redaction installed first."""
    install_redaction()
    root = logging.getLogger()
    root.setLevel(level.upper())
    if not any(getattr(h, "_midataworks", False) for h in root.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        handler._midataworks = True  # type: ignore[attr-defined]
        root.addHandler(handler)
