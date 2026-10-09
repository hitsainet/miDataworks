"""A registered secret never appears in a log record (Foundation task 3.6).

Mutation control (8.7 list): remove ``install_redaction()`` from ``register_secret`` and from
``configure_logging`` — these tests must turn red.
"""

from __future__ import annotations

import logging

import pytest

from src.core.logging import REDACTED, redact, register_secret

SECRET = "hf_SuperSecretTokenValue1234567890"


def test_a_registered_secret_never_reaches_caplog(caplog: pytest.LogCaptureFixture) -> None:
    register_secret(SECRET)
    log = logging.getLogger("some.library")
    with caplog.at_level(logging.INFO):
        log.info("token is %s", SECRET)
        log.info(f"inline {SECRET}")
        log.warning("in a dict %r", {"token": SECRET})
    assert SECRET not in caplog.text
    for record in caplog.records:
        assert SECRET not in record.getMessage()
        assert SECRET not in str(record.msg)
        assert record.args in (None, ()) or SECRET not in repr(record.args)
    assert caplog.text.count(REDACTED) == 3


def test_a_secret_in_an_exception_traceback_is_redacted(caplog: pytest.LogCaptureFixture) -> None:
    register_secret(SECRET)
    with caplog.at_level(logging.ERROR):
        try:
            raise ValueError(f"bad credential {SECRET}")
        except ValueError:
            logging.getLogger("x").exception("failed")
    assert SECRET not in caplog.text
    assert "bad credential" in caplog.text


def test_unregistered_text_is_left_alone(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        logging.getLogger("x").info("an ordinary message %d", 5)
    assert "an ordinary message 5" in caplog.text


def test_short_values_are_not_registered() -> None:
    register_secret("abc")
    assert redact("abc abc") == "abc abc"
