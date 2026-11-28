"""The backend's runner client against the REAL runner (feature 018; FTASKS 6.1, M4).

``src.clients.runner_client`` is what the API and the workers use; here it talks to the subprocess
runner over HTTP, so the two halves of the text rule meet: the client replaces lone surrogates, the
runner refuses any it is sent (``text_invalid``). Removing the replacement from the client (M4) turns
:func:`test_a_lone_surrogate_reaches_the_runner_as_u_fffd` red with the runner's own refusal.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.clients import runner_client as rc
from src.runner_contract.cases import load_cases

CASES = load_cases()
DOCUMENTS = CASES["documents"]
FLOOR = json.loads(
    (
        Path(__file__).resolve().parents[2] / "backend/src/schemas/data/midataworks-runner-v1.json"
    ).read_text(encoding="utf-8")
)["limits_floor"]
LIMITS = {
    "call_fuel": FLOOR["call_fuel"],
    "load_fuel": FLOOR["load_fuel"],
    "prelude_fuel": FLOOR["prelude_fuel"],
    "memory_bytes": FLOOR["memory_bytes"],
    "input_code_points": FLOOR["input_code_points"],
    "output_bytes": FLOOR["output_bytes"],
    "wall_ms": 10_000,
}


def _client(runner_process: Any, **kw: Any) -> rc.RunnerClient:
    return rc.RunnerClient(runner_process.base_url, **kw)


def test_a_lone_surrogate_reaches_the_runner_as_u_fffd(runner_process: Any) -> None:
    case = next(c for c in CASES["conformance"] if c["id"] == "conf.lone_surrogate")
    assert "\ud800" in case["text"]
    answer = _client(runner_process).run(
        rc.DocumentRef.of(DOCUMENTS[case["doc"]]),
        [rc.RunCall(case["function"], case["params"], [case["text"]])],
        mode="stored",
        lane="batch",
        limits=LIMITS,
        send_document=True,
    )
    assert answer.results == [[case["expect"]]]


def test_the_runner_itself_refuses_an_unreplaced_surrogate(runner_process: Any) -> None:
    """The other half: what M4 would meet. Sent raw (bypassing the client), the runner refuses."""
    import httpx

    doc = rc.DocumentRef.of(DOCUMENTS["count_ascii_digits"])
    body = rc.run_body(
        doc,
        [rc.RunCall("f", {}, ["1"])],
        mode="stored",
        lane="batch",
        limits=LIMITS,
        include_document=True,
    )
    body["calls"][0]["texts"] = ["pre \ud800 post"]
    raw = json.dumps(body).encode()  # json escapes the surrogate as \ud800; the runner decodes it
    response = httpx.post(
        runner_process.base_url + "/v1/run",
        content=raw,
        headers={"content-type": "application/json"},
        timeout=30,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "text_invalid"


def test_the_document_travels_by_hash_after_the_first_send(runner_process: Any) -> None:
    doc = rc.DocumentRef.of(DOCUMENTS["fixture2_digit_sum"])
    client = _client(runner_process)
    call = rc.RunCall("digit_sum", {"sums": [10, 20, 30]}, ["28 73 46 11"], repeat=2)
    first = client.run(doc, [call], mode="stored", lane="batch", limits=LIMITS)
    assert first.attempts == 2, "an uncached hash is refused once, then sent with its bytes"
    assert first.results == [[{"count": 3}, {"count": 3}]]
    assert first.contract_version == "1.0" and first.bundle_digest
    second = client.run(doc, [call], mode="stored", lane="batch", limits=LIMITS)
    assert second.attempts == 1 and second.results == first.results


def test_diagnostics_only_in_dry_run(runner_process: Any) -> None:
    doc = rc.DocumentRef.of(DOCUMENTS["raises"])
    client = _client(runner_process)
    dry = client.run(
        doc,
        [rc.RunCall("f", {}, ["x"])],
        mode="dry_run",
        lane="interactive",
        limits=LIMITS,
        send_document=True,
    )
    stored = client.run(
        doc, [rc.RunCall("f", {}, ["x"])], mode="stored", lane="batch", limits=LIMITS
    )
    assert dry.results[0][0]["reason"] == "rule_error" and "diagnostic" in dry.results[0][0]
    assert stored.results == [[{"reason": "rule_error"}]]


def test_a_named_refusal_is_raised(runner_process: Any) -> None:
    doc = rc.DocumentRef.of(DOCUMENTS["return_zero"])
    with pytest.raises(rc.RunnerRefused) as caught:
        _client(runner_process).run(
            doc,
            [rc.RunCall("f", {}, ["x"])],
            mode="stored",
            lane="batch",
            limits={**LIMITS, "call_fuel": FLOOR["call_fuel"] - 1},
            send_document=True,
        )
    assert caught.value.code == "limit_below_floor"


def test_inspect_and_health(runner_process: Any) -> None:
    client = _client(runner_process)
    assert client.inspect(rc.DocumentRef.of(DOCUMENTS["two_functions"])) == {
        "functions": ["a", "b"],
        "imports": [],
    }
    health = client.health()
    assert health is not None and health["status"] == "ok"


def test_an_absent_runner_is_unavailable_within_the_budget() -> None:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    client = rc.RunnerClient(f"http://127.0.0.1:{port}", budget_s=0.5)
    answer = client.run(
        rc.DocumentRef.of(DOCUMENTS["return_zero"]),
        [rc.RunCall("f", {}, ["x", "y"])],
        mode="stored",
        lane="batch",
        limits=LIMITS,
    )
    assert answer.available is False
    assert answer.results == [[{"reason": "rule_worker_unavailable"}] * 2]
    assert client.health() is None
