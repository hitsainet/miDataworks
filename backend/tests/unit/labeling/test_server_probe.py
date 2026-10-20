"""Server kind, resident model, lease support and the capability check (005 FTASKS 5.7, 5.8)."""

from __future__ import annotations

from typing import Any

from src.clients.endpoint_caller import EndpointCaller
from src.services import server_probe
from src.services.endpoint_resolver import ResolvedRoleEndpoint
from tests.support.fake_millm import ORIGIN, TEI_ORIGIN


def test_resident_model_comes_from_api_models_not_the_health_message(
    caller: EndpointCaller, fakes: Any
) -> None:
    millm, _ = fakes
    millm.queue["queue_pending"] = 3
    original = millm.handle

    def lying_health(request: Any) -> Any:
        response = original(request)
        if request.url.path == "/api/health/detailed":
            body = response.json()
            body["model_name"] = "Model Qwen2.5-7B is loaded"
            import httpx

            return httpx.Response(200, json=body)
        return response

    millm.handle = lying_health  # type: ignore[method-assign]
    info = server_probe.detect_server(caller)
    assert info.raw_health["model_name"] == "Model Qwen2.5-7B is loaded"  # the lie WAS served
    assert info.kind == "millm"
    assert info.resident is not None and info.resident.name == "JEV-9B-decision"
    assert info.resident.id == 7
    assert info.lease_supported is True
    assert info.queue == {
        "queue_pending": 3,
        "in_flight": 0,
        "queue_waiting": 0,
        "batch_backlog_rows": None,
        "estimated_wait_seconds": None,
    }
    assert (
        server_probe.model_revision(info, "JEV-9B-decision")
        == "b63f651ce8ed64481d3f5e73ecdb05f740042f01"
    )
    assert server_probe.model_revision(info, "Other") is None


def test_tei_and_generic(tei_caller: EndpointCaller, fakes: Any) -> None:
    info = server_probe.detect_server(tei_caller)
    assert info.kind == "tei" and info.tei_model_sha is not None


def _resolved(protocol: str, model: str, base: str = ORIGIN + "/v1") -> ResolvedRoleEndpoint:
    return ResolvedRoleEndpoint("classifier", protocol, base, model, None)


def test_check_millm_with_lease_surface(caller: EndpointCaller, fakes: Any) -> None:
    result = server_probe.check(_resolved("openai_scoring", "JEV-9B-decision"), caller)
    assert result.reachable and result.protocol_ok and result.model_listed
    assert result.server_kind == "millm" and result.resident_model == "JEV-9B-decision"
    assert result.lease_supported is True and result.lease_state == "free"
    assert result.error_code is None


def test_check_millm_without_lease_and_wrong_model(caller: EndpointCaller, fakes: Any) -> None:
    millm, _ = fakes
    millm.lease_supported = False
    result = server_probe.check(_resolved("openai_scoring", "Qwen2.5-7B"), caller)
    assert result.lease_supported is False and result.lease_state == "not served"
    assert result.error_code == "MODEL_NOT_LOADED" and "Load Qwen2.5-7B in miLLM" in result.message
    assert millm.calls("/v1/completions") == []  # nothing that could load a model


def test_check_tei(tei_caller: EndpointCaller, fakes: Any) -> None:
    _, tei = fakes
    result = server_probe.check(
        _resolved("tei_classification", tei.model_id, TEI_ORIGIN), tei_caller
    )
    assert result.server_kind == "tei" and result.protocol_ok and result.model_listed


def test_distinct_error_codes() -> None:
    import httpx

    from src.clients import endpoint_caller as ec

    cases = {
        "ENDPOINT_UNREACHABLE": lambda r: (_ for _ in ()).throw(
            httpx.ConnectError("no", request=r)
        ),
        "ENDPOINT_UNAUTHORIZED": lambda r: (
            httpx.Response(401, json={"error": "no"})
            if r.url.path == "/v1/models"
            else httpx.Response(404, json={})
        ),
        "ENDPOINT_NOT_JSON": lambda r: (
            httpx.Response(200, text="<html>", headers={"content-type": "text/html"})
            if r.url.path == "/v1/models"
            else httpx.Response(404, json={})
        ),
        "PROTOCOL_UNSUPPORTED": lambda r: (
            httpx.Response(200, json={"data": [{"id": "m"}]})
            if r.url.path == "/v1/models"
            else (
                httpx.Response(200, json={"choices": [{"text": "x"}]})
                if r.url.path == "/v1/completions"
                else httpx.Response(404, json={})
            )
        ),
    }
    for code, handler in cases.items():
        previous = ec.install_transport(lambda origin, h=handler: httpx.MockTransport(h))
        try:
            with EndpointCaller(
                "http://x.test/v1", None, sleep=lambda s: None, transient_retries=0
            ) as c:
                result = server_probe.check(_resolved("openai_scoring", "m", "http://x.test/v1"), c)
        finally:
            ec.install_transport(previous)
        assert result.error_code == code, (code, result)
