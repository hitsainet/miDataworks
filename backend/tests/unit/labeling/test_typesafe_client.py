"""``typesafe_systemone`` (FR-005.56; D58): the client against a fake TypeSafe server through the
real ``EndpointCaller``."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from src.clients import endpoint_caller
from src.clients.endpoint_caller import EndpointCaller
from src.clients.endpoint_errors import EndpointUnauthorized, RowError, TransientError
from src.clients.labelers.base import ProtocolUnsupported, RenderedInput
from src.clients.labelers.factory import build_classifier
from src.clients.labelers.typesafe import TypeSafeClient
from src.clients.model_listing import fetch_models
from src.core.key_binding import url_origin
from src.schemas.labeling import TypeSafeSystemOneTemplate
from tests.support.fake_typesafe import KEY, ORIGIN, FakeTypeSafe

TEMPLATE = {
    "kind": "typesafe_systemone",
    "render": "{text}",
    "input_fields": ["text"],
    "instructions": "{question}",
    "criteria": {"humorous": "Most readers would find it funny.", "not_humorous": None},
    "label_set": ["humorous", "not_humorous"],
    "positive_class": "humorous",
}
ROW = RenderedInput("rk-1", {"text": "a joke about cats"}, "Would most readers find this funny?")


@pytest.fixture
def fake() -> Iterator[FakeTypeSafe]:
    server = FakeTypeSafe()
    previous = endpoint_caller.install_transport(lambda origin: server.transport())
    yield server
    endpoint_caller.install_transport(previous)


def caller(waits: list[float] | None = None, key: str = KEY) -> EndpointCaller:
    sleep = (lambda s: waits.append(s)) if waits is not None else (lambda s: None)
    return EndpointCaller(ORIGIN + "/v1/systemone", key, sleep=sleep, key_origin=url_origin(ORIGIN))


def client(model: str | None = None, **kw: Any) -> TypeSafeClient:
    template = TypeSafeSystemOneTemplate.model_validate(TEMPLATE)
    built = build_classifier(caller(**kw), "typesafe_systemone", template, model or "jev-latest")
    assert isinstance(built, TypeSafeClient)
    return built


def test_the_exact_request_and_no_millm_header(fake: FakeTypeSafe) -> None:
    result = client().score(ROW)
    (sent,) = fake.requests
    assert (sent.method, sent.path) == ("POST", "/v1/systemone")
    assert sent.headers["authorization"] == f"Bearer {KEY}"
    assert not [h for h in sent.headers if h.lower().startswith("x-millm")]
    assert sent.body == {
        "state": "a joke about cats",
        "model": "jev-latest",
        "questions": {
            "label": {
                "type": "choice",
                "instructions": "Would most readers find this funny?",
                "criteria": {"humorous": "Most readers would find it funny.", "not_humorous": None},
            }
        },
    }
    assert result.request_body == sent.body


def test_the_answer_is_parsed_and_recorded(fake: FakeTypeSafe) -> None:
    result = client().score(ROW)
    assert result.distribution == {"humorous": 0.8, "not_humorous": 0.2}
    assert result.raw_output["confidence"] == 0.6 and result.raw_output["model"] == "jev-1.13.0"
    assert result.raw_output["usage"] == {"input_tokens": 117, "output_tokens": 34}
    assert result.raw_output["request_id"] == "req-1" and result.prompt_tokens == 117
    # an alias pins the first response's version as the run's fingerprint
    assert result.response_model is None and result.system_fingerprint == "jev-1.13.0"


def test_a_versioned_model_is_compared_as_the_response_model(fake: FakeTypeSafe) -> None:
    result = client("jev-1.13.0").score(ROW)
    assert result.response_model == "jev-1.13.0" and result.system_fingerprint is None


@pytest.mark.parametrize(
    ("answer", "error"),
    [
        ({"label": {"type": "noul", "noul": 0.3}}, ProtocolUnsupported),
        (
            {"label": {"type": "choice", "probabilities": {"yes": 0.5, "no": 0.5}}},
            RowError,
        ),
        (
            {"label": {"type": "choice", "probabilities": {"humorous": 0.5, "not_humorous": 0.2}}},
            RowError,
        ),
    ],
)
def test_answers_that_do_not_fit_the_template_are_refused(
    fake: FakeTypeSafe, answer: dict[str, Any], error: type[Exception]
) -> None:
    fake.script = [(200, {}, {"model": "jev-1.13.0", "answers": answer, "usage": {}})]
    with pytest.raises(error):
        client().score(ROW)


def test_a_refused_key_and_a_refused_row(fake: FakeTypeSafe) -> None:
    with pytest.raises(EndpointUnauthorized):
        client(key="wrong").score(ROW)
    fake.script = [(422, {}, {"error": {"message": "questions is required"}})]
    with pytest.raises(RowError):
        client().score(ROW)


def test_429_waits_as_told_and_529_backs_off(fake: FakeTypeSafe) -> None:
    waits: list[float] = []
    fake.script = [
        (429, {"retry-after-ms": "1500"}, {"error": {"message": "slow down"}}),
        (429, {"Retry-After": "2"}, {"error": {"message": "slow down"}}),
        (529, {}, {"error": {"message": "overloaded"}}),
    ]
    result = client(waits=waits).score(ROW)
    assert result.distribution["humorous"] == 0.8
    assert waits[:2] == [1.5, 2.0] and len(waits) == 3
    assert len(fake.requests) == 4


def test_endless_429_stops_resumable(fake: FakeTypeSafe) -> None:
    fake.script = [(429, {}, {"error": {"message": "slow down"}})] * 10
    with pytest.raises(TransientError):
        client(waits=[]).score(ROW)
    assert len(fake.requests) == 6  # the first try and five waits


def test_the_template_refuses_labels_that_are_not_its_criteria() -> None:
    with pytest.raises(ValueError, match="criteria"):
        TypeSafeSystemOneTemplate.model_validate({**TEMPLATE, "label_set": ["a", "b"]})


async def test_fetch_models_reads_typesafe_names() -> None:
    server = FakeTypeSafe()
    transport = httpx.MockTransport(server.handle)

    class Async(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            await request.aread()
            response = transport.handle_request(request)
            response.read()
            return response

    listing = await fetch_models(
        ORIGIN + "/v1/systemone", KEY, key_origin=url_origin(ORIGIN), transport=Async()
    )
    assert listing.source == "typesafe" and listing.models == ["jev-latest", "jev-preview"]
