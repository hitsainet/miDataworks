"""The miLLM probe-score client (009 FTASKS 12.3): one input per call, one user turn, the
endpoint etiquette, verdicts kept verbatim, refusals named."""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
import pytest

from src.clients import endpoint_caller
from src.clients.endpoint_caller import EndpointCaller
from src.clients.endpoint_errors import ModelNotResident, RowError
from src.clients.labelers.probe_score import ProbeScoreClient, render_input
from src.services.detector_sets.verdicts import map_verdict

VERDICT = {
    "probe_id": "pr_1",
    "name": "humor",
    "window": "all",
    "score": 27.613433837890625,
    "threshold": 27.613433837890625,
    "verdict": True,
    "rung": 2,
    "rung_language": "detects on unseen tasks",
    "provisional": False,
    "threshold_revision": 1,
    "n_scored_tokens": 14,
    "not_scored_reason": None,
}


@pytest.fixture
def seen() -> Iterator[list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content)
        if body["probe_ids"] == ["pr_other_model"]:
            return httpx.Response(
                409,
                json={"error": {"code": "model_not_resident", "message": "llama is not loaded"}},
            )
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "model": {"model_id": "llama"},
                    "probes": [],
                    "skipped": [],
                    "results": [
                        {
                            "index": 0,
                            "n_tokens": 14,
                            "token_ids": [1, 2],
                            "verdicts": [VERDICT],
                            "error": None,
                        }
                    ],
                },
            },
        )

    previous = endpoint_caller.install_transport(lambda origin: httpx.MockTransport(handle))
    yield requests
    endpoint_caller.install_transport(previous)


def test_one_input_as_one_user_turn_with_the_etiquette(seen: list[httpx.Request]) -> None:
    client = ProbeScoreClient(
        EndpointCaller("http://millm.test"), ["pr_1"], lease_id="lease-secret"
    )
    out = client.score({"text": "Senate passes cheese bill"})
    [request] = seen
    body = json.loads(request.content)
    assert request.url.path == "/api/probes/score"
    assert body == {
        "probe_ids": ["pr_1"],
        "inputs": [{"messages": [{"role": "user", "content": "Senate passes cheese bill"}]}],
        "return_token_ids": True,
    }
    assert request.headers["X-miLLM-Load-Policy"] == "refuse"
    assert request.headers["X-miLLM-Strict"] == "true"
    assert request.headers["X-miLLM-Lease"] == "lease-secret"
    assert out.verdicts == [VERDICT] and out.token_ids == [1, 2]
    # a score exactly on the bar comes back verdict true and maps positive (P-03)
    assert map_verdict(out.verdicts[0]).outcome == "positive"


def test_chat_rows_keep_their_messages_and_text_is_never_sent_as_text() -> None:
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    assert render_input({"messages": msgs}) == {"messages": msgs}
    assert "text" not in render_input({"text": "hello"})
    with pytest.raises(RowError):
        render_input({"text": ""})


def test_a_different_resident_model_is_refused_with_millms_reason(
    seen: list[httpx.Request],
) -> None:
    client = ProbeScoreClient(EndpointCaller("http://millm.test"), ["pr_other_model"])
    with pytest.raises(ModelNotResident, match="llama is not loaded"):
        client.score({"text": "x"})
