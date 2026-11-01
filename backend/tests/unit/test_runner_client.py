"""The runner client against a fake runner (feature 018; FTASKS 6.1; FTDD §6.2, §8.2).

The fake answers like the real runner does (``runner/tests/test_client_against_runner.py`` drives
the same client against the real one): it refuses a lone surrogate with 422 ``text_invalid`` (so a
client that forgot the U+FFFD replacement, M4, is caught here too), answers 409
``document_not_cached`` for an unknown hash, and can be told to be down. Every test asserts what
was SENT (payload) and how many requests (count), not only what came back.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from src.clients import runner_client as rc
from src.runner_contract.reasons import Reason

DOC = {
    "derived_from": {"sources": [], "statement": "fixture"},
    "functions": [{"name": "f", "output": {"type": "count"}}],
    "params_schema": {"type": "object"},
    "runner_contract": "midataworks.runner/v1",
    "source": "def f(text, params):\n    return text.count('x')\n",
}
REF = rc.DocumentRef.of(DOC)
LIMITS = {"call_fuel": 1, "wall_ms": 1}


class FakeRunner:
    """Records every request; answers by a script of statuses (``"ok"`` = a real reply)."""

    def __init__(self, script: list[str] | None = None) -> None:
        self.script = list(script or [])
        self.requests: list[dict[str, Any]] = []
        self.cached: set[str] = set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.requests.append({"path": request.url.path, "body": body})
        step = self.script.pop(0) if self.script else "ok"
        if step == "refuse_connection":
            raise httpx.ConnectError("refused", request=request)
        if step == "timeout":
            raise httpx.ReadTimeout("slow", request=request)
        if step == "503":
            return httpx.Response(503, json={"status": "bundle_mismatch"})
        if step == "limit":
            return httpx.Response(
                422, json={"error": {"code": "limit_below_floor", "message": "x"}}
            )
        if request.url.path == "/v1/inspect":
            return httpx.Response(200, json={"functions": ["f"], "imports": []})
        doc = body["document"]
        if "canonical_utf8" in doc:
            self.cached.add(doc["code_sha256"])
        elif doc["code_sha256"] not in self.cached:
            return httpx.Response(
                409, json={"error": {"code": "document_not_cached", "message": "resend"}}
            )
        results = []
        for call in body["calls"]:
            row = []
            for text in call["texts"]:
                try:
                    text.encode("utf-8")
                except UnicodeEncodeError:
                    return httpx.Response(
                        422, json={"error": {"code": "text_invalid", "message": "surrogate"}}
                    )
                row.extend([{"count": text.count("x")}] * call["repeat"])
            results.append(row)
        return httpx.Response(
            200, json={"contract_version": "1.0", "bundle_digest": "d" * 64, "results": results}
        )


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def client(fake: Callable[[httpx.Request], httpx.Response], clock: Clock, budget: float = 10.0):
    return rc.RunnerClient(
        "http://runner.test/",
        budget_s=budget,
        transport=httpx.MockTransport(fake),
        sleep=clock.sleep,
        clock=clock,
    )


def test_a_lone_surrogate_is_replaced_before_sending_m4() -> None:
    fake = FakeRunner()
    answer = client(fake, Clock()).run(
        REF,
        [rc.RunCall("f", {}, ["x\ud800x"])],
        mode="dry_run",
        lane="interactive",
        limits=LIMITS,
        send_document=True,
    )
    assert answer.results == [[{"count": 2}]]
    assert len(fake.requests) == 1
    assert fake.requests[0]["body"]["calls"][0]["texts"] == ["x�x"]


def test_document_bytes_are_sent_only_after_document_not_cached_and_once() -> None:
    fake = FakeRunner()
    clock = Clock()
    answer = client(fake, clock).run(
        REF,
        [rc.RunCall("f", {"k": 1}, ["xx", "y"], repeat=2)],
        mode="stored",
        lane="batch",
        limits=LIMITS,
    )
    assert answer.available and answer.attempts == 2
    assert answer.results == [[{"count": 2}, {"count": 2}, {"count": 0}, {"count": 0}]]
    first, second = (r["body"] for r in fake.requests)
    assert first["document"] == {"code_sha256": REF.code_sha256}
    assert second["document"] == {
        "code_sha256": REF.code_sha256,
        "canonical_utf8": REF.canonical_utf8,
    }
    assert second == {
        "contract": "midataworks.runner/v1",
        "mode": "stored",
        "lane": "batch",
        "limits": LIMITS,
        "document": second["document"],
        "calls": [{"function": "f", "params": {"k": 1}, "texts": ["xx", "y"], "repeat": 2}],
    }
    assert clock.sleeps == [], "a resend is not a retry"
    # Cached now: the next request carries the hash only, in one request.
    client(fake, clock).run(
        REF, [rc.RunCall("f", {}, ["x"])], mode="stored", lane="batch", limits=LIMITS
    )
    assert len(fake.requests) == 3 and "canonical_utf8" not in fake.requests[2]["body"]["document"]


def test_retry_is_bounded_and_then_every_result_is_unavailable() -> None:
    fake = FakeRunner(["refuse_connection"] * 100)
    clock = Clock()
    answer = client(fake, clock, budget=3.0).run(
        REF,
        [rc.RunCall("f", {}, ["a", "b"]), rc.RunCall("f", {}, ["c"], repeat=2)],
        mode="stored",
        lane="batch",
        limits=LIMITS,
    )
    unavailable = {"reason": Reason.RULE_WORKER_UNAVAILABLE.value}
    assert answer.available is False
    assert answer.results == [[unavailable] * 2, [unavailable] * 2]
    assert answer.contract_version is None and answer.bundle_digest is None
    # 100 ms doubling, capped at 1.6 s, within 3 s: 0.1 + 0.2 + 0.4 + 0.8 = 1.5, then 1.6 > budget.
    assert clock.sleeps == [0.1, 0.2, 0.4, 0.8]
    assert len(fake.requests) == answer.attempts == 5


def test_a_503_is_retried_and_a_later_success_is_kept() -> None:
    fake = FakeRunner(["503", "503"])
    clock = Clock()
    answer = client(fake, clock).run(
        REF,
        [rc.RunCall("f", {}, ["x"])],
        mode="stored",
        lane="batch",
        limits=LIMITS,
        send_document=True,
    )
    assert answer.available and answer.results == [[{"count": 1}]]
    assert clock.sleeps == [0.1, 0.2] and len(fake.requests) == 3


def test_a_503_past_the_budget_is_the_reason_not_an_error() -> None:
    fake = FakeRunner(["503"] * 50)
    answer = client(fake, Clock(), budget=0.05).run(
        REF,
        [rc.RunCall("f", {}, ["x"])],
        mode="dry_run",
        lane="interactive",
        limits=LIMITS,
    )
    assert answer.results == [[{"reason": "rule_worker_unavailable"}]]
    assert len(fake.requests) == 1, "the first wait already exceeds a 50 ms budget"


def test_a_timeout_is_not_retried() -> None:
    fake = FakeRunner(["timeout"])
    clock = Clock()
    answer = client(fake, clock).run(
        REF,
        [rc.RunCall("f", {}, ["x"])],
        mode="stored",
        lane="batch",
        limits=LIMITS,
    )
    assert answer.available is False and len(fake.requests) == 1 and clock.sleeps == []


def test_a_named_refusal_is_raised_with_the_runners_code() -> None:
    fake = FakeRunner(["limit"])
    with pytest.raises(rc.RunnerRefused) as caught:
        client(fake, Clock()).run(
            REF,
            [rc.RunCall("f", {}, ["x"])],
            mode="stored",
            lane="batch",
            limits=LIMITS,
        )
    assert caught.value.code == "limit_below_floor" and caught.value.status == 422
    assert len(fake.requests) == 1


def test_a_reply_that_does_not_match_the_calls_is_refused() -> None:
    def short(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [[]], "contract_version": "1.0"})

    with pytest.raises(rc.RunnerRefused) as caught:
        client(short, Clock()).run(
            REF,
            [rc.RunCall("f", {}, ["x"])],
            mode="stored",
            lane="batch",
            limits=LIMITS,
            send_document=True,
        )
    assert caught.value.code == "runner_reply_invalid"


def test_inspect_and_health() -> None:
    fake = FakeRunner(["refuse_connection", "ok"])
    clock = Clock()
    c = client(fake, clock)
    assert c.inspect(REF) == {"functions": ["f"], "imports": []}
    assert [r["path"] for r in fake.requests] == ["/v1/inspect", "/v1/inspect"]
    assert fake.requests[1]["body"]["document"]["canonical_utf8"] == REF.canonical_utf8
    down = client(FakeRunner(["refuse_connection"] * 50), Clock(), budget=0.5)
    assert down.inspect(REF) is None
    assert down.health() is None


async def test_the_async_client_follows_the_same_rules() -> None:
    fake = FakeRunner(["503"])
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async_client = rc.AsyncRunnerClient(
        "http://runner.test", transport=httpx.MockTransport(fake), sleep=sleep
    )
    answer = await async_client.run(
        REF,
        [rc.RunCall("f", {}, ["x\udfffx"])],
        mode="stored",
        lane="interactive",
        limits=LIMITS,
    )
    assert answer.results == [[{"count": 2}]]
    assert sleeps == [0.1]
    assert [r["body"]["document"].get("canonical_utf8") for r in fake.requests] == [
        None,
        None,
        REF.canonical_utf8,
    ]
    assert fake.requests[-1]["body"]["calls"][0]["texts"] == ["x�x"]
    assert await async_client.inspect(REF) == {"functions": ["f"], "imports": []}
    down = rc.AsyncRunnerClient(
        "http://runner.test",
        budget_s=0.0,
        transport=httpx.MockTransport(FakeRunner(["refuse_connection"] * 5)),
        sleep=sleep,
    )
    gone = await down.run(
        REF, [rc.RunCall("f", {}, ["x"])], mode="stored", lane="batch", limits=LIMITS
    )
    assert gone.available is False and gone.results == [[{"reason": "rule_worker_unavailable"}]]
    assert await down.health() is None


def test_mode_and_lane_are_checked_before_anything_is_sent() -> None:
    fake = FakeRunner()
    c = client(fake, Clock())
    with pytest.raises(ValueError):
        c.run(REF, [rc.RunCall("f", {}, ["x"])], mode="exec", lane="batch", limits=LIMITS)
    with pytest.raises(ValueError):
        c.run(REF, [rc.RunCall("f", {}, ["x"])], mode="stored", lane="fast", limits=LIMITS)
    with pytest.raises(ValueError):
        c.run(REF, [], mode="stored", lane="batch", limits=LIMITS)
    assert fake.requests == []


@pytest.mark.parametrize("content", [b"<html>proxy error</html>", b"[1, 2]"])
def test_a_200_that_is_not_a_json_object_is_a_named_refusal_d_r2_4(content: bytes) -> None:
    """018 Review R2 D-R2-4: a 200 whose body is not a JSON object raised a bare ValueError (an
    API 500); it is ``runner_reply_invalid``, which the API reads as the runner being unavailable,
    on both clients."""
    import asyncio

    from src.clients.runner_client import (
        AsyncRunnerClient,
        DocumentRef,
        RunCall,
        RunnerClient,
        RunnerRefused,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=content)

    doc = DocumentRef("a" * 64, "{}")
    call = [RunCall("f", {}, ["t"])]
    sync = RunnerClient("http://runner.test", transport=httpx.MockTransport(handler))
    with pytest.raises(RunnerRefused) as caught:
        sync.run(doc, call, mode="stored", lane="batch", limits={})
    assert caught.value.code == "runner_reply_invalid"
    client = AsyncRunnerClient("http://runner.test", transport=httpx.MockTransport(handler))
    with pytest.raises(RunnerRefused) as caught_async:
        asyncio.run(client.run(doc, call, mode="stored", lane="batch", limits={}))
    assert caught_async.value.code == "runner_reply_invalid"
