"""A role's capability check (the Settings "Test" button) never makes miLLM load a model.

017 FTASKS 17.8 (d): the embeddings checks went out as ``probe``, which sends no
``X-miLLM-Load-Policy``; miLLM's embeddings route loads the model a request names, so testing an
embeddings role that named another model could swap the served model. Every protocol's one
minimal request is asserted here on the wire: the load-policy header, the model, and one request.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from src.clients import endpoint_caller
from src.clients.endpoint_caller import EndpointCaller
from src.services import server_probe

BASE = "http://model-server.test/v1"
MODEL = "Llama-3.1-8B-Instruct"

ANSWERS: dict[str, Any] = {
    "/predict": [{"label": "LABEL_0", "score": 0.9}],
    "/v1/completions": {"choices": [{"text": "x", "logprobs": {"top_logprobs": [{"x": -0.1}]}}]},
    "/v1/chat/completions": {"choices": [{"message": {"role": "assistant", "content": "OK"}}]},
    "/v1/embeddings": {"data": [{"index": 0, "embedding": [0.1, 0.2]}], "model": MODEL},
    "/embed": [[0.1, 0.2]],
}


@pytest.fixture
def seen() -> Any:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=ANSWERS[request.url.path])

    previous = endpoint_caller.install_transport(lambda _origin: httpx.MockTransport(handler))
    try:
        yield requests
    finally:
        endpoint_caller.install_transport(previous)


@pytest.mark.parametrize(
    ("protocol", "path"),
    [
        ("tei_classification", "/predict"),
        ("openai_scoring", "/v1/completions"),
        ("openai_chat", "/v1/chat/completions"),
        ("openai_embeddings", "/v1/embeddings"),
        ("tei_embeddings", "/embed"),
    ],
)
def test_every_protocols_minimal_request_refuses_a_load(
    seen: list[httpx.Request], protocol: str, path: str
) -> None:
    with EndpointCaller(BASE, None) as caller:
        server_probe._minimal_request(caller, protocol, MODEL)
    assert [r.url.path for r in seen] == [path]
    assert seen[0].headers.get("x-millm-load-policy") == "refuse", dict(seen[0].headers)
    if path.startswith("/v1/"):
        assert json.loads(seen[0].content)["model"] == MODEL


@pytest.mark.parametrize(
    ("protocol", "path"), [("openai_embeddings", "/v1/embeddings"), ("tei_embeddings", "/embed")]
)
def test_the_diversity_reports_embeddings_request_refuses_a_load(
    seen: list[httpx.Request], protocol: str, path: str
) -> None:
    """Both of the diversity report's embeddings protocols (the integration test drives the
    OpenAI-shaped one against the gated fake miLLM end to end)."""
    from types import SimpleNamespace

    from src.services.generation import diversity_service

    resolved = SimpleNamespace(protocol=protocol, model_id=MODEL)
    with EndpointCaller(BASE, None) as caller:
        vectors, _ = diversity_service._embed_batch(caller, resolved, ["one text"])
    assert vectors == [[0.1, 0.2]]
    assert [r.url.path for r in seen] == [path]
    assert seen[0].headers.get("x-millm-load-policy") == "refuse", dict(seen[0].headers)
