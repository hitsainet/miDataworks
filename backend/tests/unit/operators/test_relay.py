"""The loopback relay (FR-003.15, FR-003.18, FR-003.26; FTASKS 8.3, 8.7, 15.2).

Against a real loopback fake upstream (``FakeOpenAI``): headers, body, call count and records are
asserted, and the relay is reached over HTTP exactly as a caller reaches it.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from src.operators.data_designer import relay as relay_module
from src.operators.data_designer.relay import ROW_KEY_HEADER, LoopbackRelay, RelayError
from src.operators.endpoint_port import Lease, ResolvedEndpoint
from tests.support.fake_openai import FakeOpenAI, fake_openai

__all__ = ["fake_openai"]
KEY = "sk-real-NeverWriteMe-relay-0123"


def _endpoint(fake: FakeOpenAI, *, millm: bool = True) -> ResolvedEndpoint:
    return ResolvedEndpoint("generation", fake.base_url, "m1", KEY, millm)


def _post(
    relay: LoopbackRelay, text: str, row_key: str | None = "k1", **extra: Any
) -> httpx.Response:
    headers = {"Authorization": f"Bearer {relay.nonce}"}
    if row_key:
        headers[ROW_KEY_HEADER] = row_key
    body = {"model": "m1", "messages": [{"role": "user", "content": text}], **extra}
    return httpx.post(f"{relay.base_url}/chat/completions", json=body, headers=headers, timeout=30)


def test_forwards_with_the_real_key_strict_lease_and_refuse_load(fake_openai: FakeOpenAI) -> None:
    lease = Lease("m1", "lease-42")
    with LoopbackRelay(_endpoint(fake_openai), lease=lease) as relay:
        response = _post(relay, "hello")
    assert (
        response.status_code == 200
        and response.json()["choices"][0]["message"]["content"] == "echo: hello"
    )
    assert len(fake_openai.requests) == 1
    sent = fake_openai.requests[0]
    assert sent["headers"]["authorization"] == f"Bearer {KEY}"
    assert sent["headers"]["x-millm-strict"] == "true"
    assert sent["headers"]["x-millm-load-policy"] == "refuse"
    assert sent["headers"]["x-millm-lease"] == "lease-42"
    assert ROW_KEY_HEADER.lower() not in sent["headers"]


def test_a_non_millm_endpoint_gets_strict_but_no_lease_headers(fake_openai: FakeOpenAI) -> None:
    with LoopbackRelay(_endpoint(fake_openai, millm=False)) as relay:
        _post(relay, "hello")
    headers = fake_openai.requests[0]["headers"]
    assert headers["x-millm-strict"] == "true"
    assert "x-millm-load-policy" not in headers and "x-millm-lease" not in headers


def test_a_request_without_the_nonce_is_refused_and_not_forwarded(fake_openai: FakeOpenAI) -> None:
    with LoopbackRelay(_endpoint(fake_openai)) as relay:
        anonymous = httpx.post(
            f"{relay.base_url}/chat/completions", json={"model": "m"}, timeout=10
        )
        wrong = httpx.post(
            f"{relay.base_url}/chat/completions",
            json={"model": "m"},
            headers={"Authorization": f"Bearer {KEY}"},
            timeout=10,
        )
    assert anonymous.status_code == wrong.status_code == 401
    assert fake_openai.requests == []


def test_per_request_records_carry_the_raw_steering_header(fake_openai: FakeOpenAI) -> None:
    """15.2: one record per request, row key, model, revision, the RAW X-miLLM-Steering value."""
    with LoopbackRelay(_endpoint(fake_openai)) as relay:
        _post(relay, "one", "key-a")
        _post(relay, "two", None, dw_row_key="key-b")
    assert [r["row_key"] for r in relay.records] == ["key-a", "key-b"]
    assert len(relay.records) == len(fake_openai.requests) == 2
    record = relay.records[0]
    assert record["steering_header"] == "profile=p1;strength=0.4"
    assert record["revision"] == "rev-7" and record["model"] == "m1" and record["status"] == 200
    assert "dw_row_key" not in fake_openai.requests[1]["body"], "stripped before forwarding"


def test_body_overrides_are_merged_into_every_forwarded_body(fake_openai: FakeOpenAI) -> None:
    """15.2 / FR-003.26."""
    overrides = {"profile": "p1", "steering": {"strength": 0.4}}
    with LoopbackRelay(_endpoint(fake_openai), body_overrides=overrides) as relay:
        _post(relay, "a", steering={"layer": 11})
        _post(relay, "b")
    first, second = (r["body"] for r in fake_openai.requests)
    assert first["profile"] == "p1" and first["steering"] == {"layer": 11, "strength": 0.4}
    assert second["profile"] == "p1" and second["steering"] == {"strength": 0.4}


@pytest.mark.parametrize("key", ["model", "messages", "dw_row_key", "stream"])
def test_an_override_naming_a_relay_owned_field_is_refused(
    fake_openai: FakeOpenAI, key: str
) -> None:
    with pytest.raises(RelayError) as exc:
        LoopbackRelay(_endpoint(fake_openai), body_overrides={key: "x"})
    assert exc.value.code == "relay_override_conflict"


def test_503_waits_retry_after_and_is_not_a_failure(
    fake_openai: FakeOpenAI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """8.7: queue full -> wait and retry; the record shows two attempts and no failure."""
    waits: list[float] = []
    with LoopbackRelay(_endpoint(fake_openai), sleep=waits.append) as relay:
        response = _post(relay, "BUSY please")
    assert response.status_code == 200
    assert waits == [0.0] and len(fake_openai.requests) == 2
    assert relay.records[0]["attempts"] == 2 and relay.records[0]["reason"] is None


def test_unreachable_endpoint_backs_off_then_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    waits: list[float] = []
    dead = ResolvedEndpoint("generation", "http://127.0.0.1:1/v1", "m", KEY, True)
    monkeypatch.setattr(relay_module, "MAX_ATTEMPTS", 4)
    with LoopbackRelay(dead, sleep=waits.append) as relay:
        response = _post(relay, "x")
    assert response.status_code == 502
    assert waits == [0.5, 1.0, 2.0], "exponential backoff, capped"
    assert relay.records[0]["reason"] == "unreachable"


def test_context_overflow_is_a_recorded_skip_not_retried(fake_openai: FakeOpenAI) -> None:
    """8.7 / FR-005.47."""
    with LoopbackRelay(_endpoint(fake_openai)) as relay:
        response = _post(relay, "OVERFLOW " * 3, "long-row")
    assert response.status_code == 400
    assert len(fake_openai.requests) == 1
    assert (
        relay.records[0]["reason"] == "context_overflow"
        and relay.records[0]["row_key"] == "long-row"
    )


def test_retry_after_is_capped() -> None:
    relay = LoopbackRelay(ResolvedEndpoint("g", "http://x/v1", "m", None))
    response = httpx.Response(503, headers={"Retry-After": "9999"})
    assert relay._retry_after(response, 1) == relay_module.MAX_RETRY_AFTER_S


def test_the_key_is_dropped_on_exit_and_never_in_records(fake_openai: FakeOpenAI) -> None:
    relay = LoopbackRelay(_endpoint(fake_openai))
    with relay:
        _post(relay, "hello")
    assert relay._key is None
    assert KEY not in repr(relay.records) and KEY not in repr(_endpoint(fake_openai))
