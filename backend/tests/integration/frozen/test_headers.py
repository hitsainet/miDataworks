"""DR-08: the ``X-miLLM-*`` headers miDataworks sends and reads stay (feature 020; FR-020.2).

miServer keeps answering to these names (they are its public API). The real generation call (the
endpoint caller every labeler and generator uses) runs against a fake miServer on loopback, and the
Data Designer relay against a fake OpenAI server; each literal is asserted where it travels."""

from __future__ import annotations

from typing import Any

import httpx

from src.operators.data_designer.relay import LoopbackRelay
from src.operators.endpoint_port import Lease, ResolvedEndpoint
from src.services.generation.generation_call import CallContext
from src.services.generation.run_service import call_for
from tests.contract.test_generation_relay import requests, server  # noqa: F401 - fixture
from tests.support.fake_openai import FakeOpenAI, fake_openai  # noqa: F401 - fixture
from tests.support.frozen import exercises
from tests.support.generation_fixtures import GEN_MODEL, FakeMillmServer

SENT = ("X-miLLM-Strict", "X-miLLM-Load-Policy", "X-miLLM-Lease")
READ = ("X-miLLM-Seed", "X-miLLM-Steering")
RELAY_READ = ("X-miLLM-Model-Revision",)


def _generate(running: FakeMillmServer) -> list[Any]:
    ctx = CallContext(
        base_url=running.base_url + "/v1",
        model=GEN_MODEL,
        api_key="sk-x",
        lease_id="lease-frozen",
        is_millm=True,
        body_overrides={"steering": {"features": []}},
    )
    return call_for("native").run(requests(), ctx)


def _relay(fake: FakeOpenAI) -> list[dict[str, Any]]:
    endpoint = ResolvedEndpoint("generation", fake.base_url, "m1", None, True, key_origin=None)
    with LoopbackRelay(endpoint, lease=Lease("m1", "lease-frozen")) as relay:
        httpx.post(
            f"{relay.base_url}/chat/completions",
            json={"model": "m1", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": f"Bearer {relay.nonce}", "X-Dataworks-Row-Key": "k1"},
            timeout=30,
        )
        return list(relay.records)


@exercises("DR-08", literals=(*SENT, *READ, *RELAY_READ))
def test_dr08_headers_sent_by_the_real_paths(
    literal: str,
    server: FakeMillmServer,  # noqa: F811
    fake_openai: FakeOpenAI,  # noqa: F811
) -> None:
    if literal in RELAY_READ:
        [record] = _relay(fake_openai)
        assert record["revision"] == "rev-7", "the relay reads the revision under this name"
        sent = fake_openai.requests[0]["headers"]
        assert sent["x-millm-strict"] == "true" and sent["x-millm-lease"] == "lease-frozen"
        return
    results = _generate(server)
    calls = [r for r in server.fake.requests if r.path == "/v1/chat/completions"]
    assert len(calls) == len(results) == 3
    if literal in SENT:
        expected = {"X-miLLM-Strict": "true", "X-miLLM-Load-Policy": "refuse"}.get(
            literal, "lease-frozen"
        )
        assert [c.headers.get(literal.lower()) for c in calls] == [expected] * 3
    elif literal == "X-miLLM-Seed":
        assert [r.seed_echo for r in results] == [100, 101, 102], "the seed echo is read"
    else:
        assert all(r.steering_header for r in results), "the steering state is read"
