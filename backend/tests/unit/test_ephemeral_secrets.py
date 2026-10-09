"""The ephemeral token store (001 FTASKS 3.3, 3.4; T-05).

Round trip; a second ``take`` returns None; expiry; a second ``put`` on one key is refused; the
value in Redis is not the plain token; the value is registered with the log redactor.
"""

from __future__ import annotations

import logging
import time

import pytest

from src.core import ephemeral_secrets


def _key() -> str:
    return f"test-{time.time_ns()}"


def test_round_trip_then_gone() -> None:
    key = _key()
    ephemeral_secrets.put(key, "hf_secret_value_123", ttl_s=60)
    assert ephemeral_secrets.take(key) == "hf_secret_value_123"
    assert ephemeral_secrets.take(key) is None


def test_the_stored_value_is_not_the_plain_token() -> None:
    key = _key()
    ephemeral_secrets.put(key, "hf_secret_value_456", ttl_s=60)
    raw = ephemeral_secrets.redis_client().get(ephemeral_secrets.PREFIX + key)
    assert raw is not None
    assert b"hf_secret_value_456" not in raw
    ephemeral_secrets.take(key)


def test_the_store_has_a_lifetime() -> None:
    key = _key()
    ephemeral_secrets.put(key, "hf_secret_ttl", ttl_s=60)
    ttl = ephemeral_secrets.redis_client().ttl(ephemeral_secrets.PREFIX + key)
    assert 0 < ttl <= 60
    ephemeral_secrets.take(key)


def test_it_expires() -> None:
    key = _key()
    ephemeral_secrets.put(key, "hf_short_lived", ttl_s=1)
    time.sleep(1.3)
    assert ephemeral_secrets.take(key) is None


def test_a_second_put_is_refused_and_the_first_kept() -> None:
    key = _key()
    ephemeral_secrets.put(key, "hf_first_value", ttl_s=60)
    with pytest.raises(ephemeral_secrets.EphemeralKeyTaken):
        ephemeral_secrets.put(key, "hf_second_value", ttl_s=60)
    assert ephemeral_secrets.take(key) == "hf_first_value"


def test_a_value_sealed_under_one_key_does_not_open_under_another() -> None:
    first, second = _key(), _key() + "b"
    ephemeral_secrets.put(first, "hf_bound_value", ttl_s=60)
    sealed = ephemeral_secrets.redis_client().get(ephemeral_secrets.PREFIX + first)
    ephemeral_secrets.redis_client().set(ephemeral_secrets.PREFIX + second, sealed, ex=60)
    with pytest.raises(Exception):  # noqa: B017 - InvalidTag from the AEAD
        ephemeral_secrets.take(second)
    ephemeral_secrets.take(first)


def test_a_put_value_is_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    key = _key()
    ephemeral_secrets.put(key, "hf_never_in_a_log_line", ttl_s=60)
    logging.getLogger("test").warning("request carried %s", "hf_never_in_a_log_line")
    assert "hf_never_in_a_log_line" not in caplog.text
    ephemeral_secrets.take(key)


def test_take_registers_the_value_with_the_redactor_in_the_worker_process(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The API ``put``s and the worker ``take``s, in DIFFERENT processes: the worker learns the
    value only from ``take``, so ``take`` itself must register it (in tests both run in one process,
    where ``put`` has already registered it — the registration is forgotten here to model that)."""
    from src.core import logging as dw_logging

    value, key = "hf_worker_side_token_5150", _key()
    ephemeral_secrets.put(key, value, ttl_s=60)
    with dw_logging._lock:
        dw_logging._secrets.discard(value)  # the worker process never saw the put
    assert dw_logging.redact(value) == value
    assert ephemeral_secrets.take(key) == value
    with caplog.at_level(logging.INFO):
        logging.getLogger("worker").info("loading with %s", value)
    assert value not in caplog.text
