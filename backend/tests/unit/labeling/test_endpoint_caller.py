"""The call wrapper's etiquette and status mapping (005 FTASKS 5.4 – 5.6; FPRD 005 criterion 7).

Every test asserts the headers sent, the body and the CALL COUNT: "was called" passes against a
call sending the wrong arguments. Waits go through a fake sleep (a simulated clock).
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from src.clients import endpoint_caller as ec
from src.clients.endpoint_errors import (
    ContextOverflow,
    EndpointNotJson,
    EndpointUnauthorized,
    EndpointUnreachable,
    LeaseLost,
    ModelNotResident,
    RowError,
    StrictRefusal,
    TransientError,
)

SRC = Path(__file__).resolve().parents[3] / "src"


class Script:
    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        return item


def ok(body: Any = None) -> httpx.Response:
    return httpx.Response(200, json=body if body is not None else {"ok": True})


def err(status: int, code: str | None, message: str = "x", **headers: str) -> httpx.Response:
    return httpx.Response(
        status, json={"error": {"message": message, "type": "t", "code": code}}, headers=headers
    )


@pytest.fixture
def make(monkeypatch: pytest.MonkeyPatch) -> Any:
    def build(script: Script, **kw: Any) -> tuple[ec.EndpointCaller, list[float]]:
        waits: list[float] = []
        previous = ec.install_transport(lambda origin: httpx.MockTransport(script))
        monkeypatch.setattr(ec, "_transport_factory", ec._transport_factory)
        caller = ec.EndpointCaller(
            "http://m.test/v1", kw.pop("api_key", "sk-key-123456"), sleep=waits.append, **kw
        )
        ec.install_transport(previous)
        caller._client = httpx.Client(transport=httpx.MockTransport(script))
        return caller, waits

    return build


def call(caller: ec.EndpointCaller, purpose: Any = "scoring", lease: str | None = None) -> Any:
    return caller.call(
        "POST", "/v1/completions", body={"model": "m"}, purpose=purpose, openai=True, lease_id=lease
    )


def test_etiquette_headers_on_scoring(make: Any) -> None:
    script = Script(ok())
    caller, _ = make(script)
    call(caller, lease="lease-abc-123")
    (req,) = script.requests
    assert req.headers["X-miLLM-Strict"] == "true"
    assert req.headers["X-miLLM-Load-Policy"] == "refuse"
    assert req.headers["X-miLLM-Lease"] == "lease-abc-123"
    assert req.headers["Authorization"] == "Bearer sk-key-123456"
    assert json.loads(req.content) == {"model": "m"}
    assert str(req.url) == "http://m.test/v1/completions"


def test_no_lease_header_without_a_ticket_and_no_refuse_on_management(make: Any) -> None:
    script = Script(ok())
    caller, _ = make(script, api_key=None)
    caller.raw("GET", "/api/models")
    (req,) = script.requests
    assert "X-miLLM-Lease" not in req.headers
    assert "X-miLLM-Load-Policy" not in req.headers
    assert "Authorization" not in req.headers


def test_judge_and_generation_refuse_load(make: Any) -> None:
    for purpose in ("judge", "generation"):
        script = Script(ok())
        caller, _ = make(script)
        call(caller, purpose=purpose)
        assert script.requests[0].headers["X-miLLM-Load-Policy"] == "refuse"


def test_503_with_retry_after_waits_at_least_that_long(make: Any) -> None:
    script = Script(err(503, "queue_full", **{"Retry-After": "3"}), ok())
    caller, waits = make(script)
    call(caller)
    assert waits == [3.0]
    assert len(script.requests) == 2


def test_503_without_retry_after_backs_off_1_2_4_capped(make: Any) -> None:
    script = Script(*[err(503, "queue_full")] * 8, ok())
    caller, waits = make(script, backoff_cap_s=10.0)
    call(caller)
    assert waits == [1.0, 2.0, 4.0, 8.0, 10.0, 10.0, 10.0, 10.0]
    assert len(script.requests) == 9


def test_backpressure_never_raises_and_on_wait_sees_each_wait(make: Any) -> None:
    seen: list[float] = []
    script = Script(err(503, "queue_full", **{"Retry-After": "2"}), ok())
    caller, _ = make(script, on_wait=lambda s, reason: seen.append(s))
    call(caller)
    assert seen == [2.0]


@pytest.mark.parametrize(
    ("response", "exc"),
    [
        (err(409, "model_not_resident"), ModelNotResident),
        (err(409, "model_leased"), LeaseLost),
        (err(400, "unused_fields_refused"), StrictRefusal),
        (err(400, "context_length_exceeded"), ContextOverflow),
        (err(401, None), EndpointUnauthorized),
        (err(422, "invalid_parameter"), RowError),
        (
            httpx.Response(
                413,
                json={
                    "error": "Input validation error: must have less than 512 tokens",
                    "error_type": "Validation",
                },
            ),
            ContextOverflow,
        ),
    ],
)
def test_mapping_raises_after_exactly_one_call(
    make: Any, response: httpx.Response, exc: type
) -> None:
    script = Script(response, ok())
    caller, waits = make(script)
    with pytest.raises(exc):
        call(caller)
    assert len(script.requests) == 1
    assert waits == []


def test_transient_5xx_retried_three_times_then_raised(make: Any) -> None:
    script = Script(err(500, "server_error"))
    caller, waits = make(script)
    with pytest.raises(TransientError):
        call(caller)
    assert len(script.requests) == 4
    assert waits == [1.0, 2.0, 4.0]


def test_transient_then_success(make: Any) -> None:
    script = Script(err(502, None), ok({"v": 1}))
    caller, _ = make(script)
    assert call(caller).body == {"v": 1}
    assert len(script.requests) == 2


def test_unreachable_after_retries(make: Any) -> None:
    script = Script(httpx.ConnectError("refused"))
    caller, _ = make(script, transient_retries=1)
    with pytest.raises(EndpointUnreachable):
        call(caller)
    assert len(script.requests) == 2


def test_html_200_is_not_json(make: Any) -> None:
    script = Script(httpx.Response(200, text="<html>", headers={"content-type": "text/html"}))
    caller, _ = make(script)
    with pytest.raises(EndpointNotJson):
        call(caller)


def test_the_key_is_redacted_from_logs(make: Any, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    caller, _ = make(Script(ok()), api_key="sk-very-secret-key-xyz")
    logging.getLogger("t").warning("calling with sk-very-secret-key-xyz")
    assert "sk-very-secret-key-xyz" not in caplog.text
    assert "sk-very-secret-key-xyz" not in repr(caller)


def test_only_the_caller_imports_httpx_among_labeling_modules() -> None:
    """005 FTASKS 5.6: etiquette lives in ONE place."""
    labeling = [
        *sorted((SRC / "clients" / "labelers").glob("*.py")),
        SRC / "clients" / "millm_lease_client.py",
        SRC / "clients" / "millm_batch_client.py",
        *[
            SRC / "services" / f
            for f in (
                "endpoint_resolver.py",
                "server_probe.py",
                "label_run_engine.py",
                "label_run_service.py",
                "label_store.py",
                "label_inputs.py",
                "model_lease_holder.py",
                "labeling_rules.py",
                "decision_template_service.py",
                "labeling_ports.py",
            )
        ],
    ]
    offenders = []
    for path in labeling:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any(n == "httpx" or n.startswith("httpx.") for n in names):
                offenders.append(path.name)
    assert offenders == []
