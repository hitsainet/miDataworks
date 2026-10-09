"""007's relay path end to end against a fake miLLM on loopback (007 FTASKS 13.2; FTDD 007 10.1).

003's relay (FR-003.26) carries the step's ``body_overrides`` into every forwarded body, records
the RAW ``X-miLLM-Steering`` and ``X-miLLM-Seed`` per row key, and 007 joins each record back to
its request and hands the verbatim header to ``check_reported_state``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from src.services.generation import steering
from src.services.generation.generation_call import (
    CallContext,
    GenerationRequest,
    NativeGenerationCall,
    RelayGenerationCall,
)
from src.services.generation.run_service import call_for
from tests.support.generation_fixtures import GEN_MODEL, SAE, FakeMillmServer, default_fake


@pytest.fixture
def server(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeMillmServer]:
    from src.clients import endpoint_caller
    from src.operators import endpoint_port
    from src.operators.endpoint_port import ResolvedEndpoint

    fake = default_fake()
    previous = endpoint_caller.install_transport(None)
    with FakeMillmServer(fake) as running:

        class Resolver:
            def resolve(self, role: str) -> ResolvedEndpoint:
                return ResolvedEndpoint(role, running.base_url + "/v1", GEN_MODEL, "sk-x", True)

        monkeypatch.setattr(endpoint_port, "_resolver", Resolver())
        try:
            yield running
        finally:
            endpoint_caller.install_transport(previous)


def requests() -> list[GenerationRequest]:
    return [
        GenerationRequest(
            i, f"k{i}", [{"role": "user", "content": f"q {i}"}], {"max_tokens": 8}, 100 + i
        )
        for i in range(3)
    ]


def test_the_relay_carries_overrides_and_returns_each_raw_header(server: FakeMillmServer) -> None:
    overrides = {"steering": {"sae_id": SAE, "features": [{"index": 3, "strength": 2.0}]}}
    ctx = CallContext(
        base_url=server.base_url + "/v1",
        model=GEN_MODEL,
        api_key="sk-x",
        lease_id="lease-test",
        is_millm=True,
        body_overrides=overrides,
    )
    results = RelayGenerationCall().run(requests(), ctx)
    calls = [r for r in server.fake.requests if r.path == "/v1/chat/completions"]
    assert [c.body["seed"] for c in calls] == [100, 101, 102]
    assert all(c.body["steering"] == overrides["steering"] for c in calls)
    assert all(c.headers["x-millm-lease"] == "lease-test" for c in calls)
    assert all(c.headers["x-millm-load-policy"] == "refuse" for c in calls)
    assert all("dw_row_key" not in c.body and "record_index" not in c.body for c in calls)
    expected = steering.Expected(
        "inline",
        sae_id=SAE,
        layer=11,
        features=((3, 2.0),),
        set_hash=steering.applied_set_hash(SAE, [(3, 2.0)]),
    )
    for result in results:
        assert result.steering_header and result.steering_header.startswith("inline;")
        assert steering.check_header(expected, result.steering_header).matched
        assert result.seed_echo == 100 + result.record_index
        assert result.text and f"q {result.record_index}" in result.text


def test_both_paths_return_identical_results(server: FakeMillmServer) -> None:
    ctx = CallContext(
        base_url=server.base_url + "/v1",
        model=GEN_MODEL,
        api_key="sk-x",
        is_millm=True,
        body_overrides={"steering": {"features": []}},
    )
    relay = RelayGenerationCall().run(requests(), ctx)
    native = call_for("native").run(requests(), ctx)
    assert isinstance(call_for("native"), NativeGenerationCall)
    keep = (
        "record_index",
        "text",
        "finish_reason",
        "model",
        "steering_header",
        "seed_echo",
        "outcome",
    )
    assert [{k: getattr(r, k) for k in keep} for r in relay] == [
        {k: getattr(r, k) for k in keep} for r in native
    ]
