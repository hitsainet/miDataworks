"""Shared fakes for feature 005 unit tests: an EndpointCaller routed to the fake miLLM/TEI."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from src.clients import endpoint_caller
from tests.support.fake_millm import ORIGIN, TEI_ORIGIN, FakeMillm, FakeTEI, route


@pytest.fixture
def fakes() -> Iterator[tuple[FakeMillm, FakeTEI]]:
    millm, tei = FakeMillm(), FakeTEI()
    previous = endpoint_caller.install_transport(route({ORIGIN: millm, TEI_ORIGIN: tei}))
    try:
        yield millm, tei
    finally:
        endpoint_caller.install_transport(previous)


@pytest.fixture
def caller(fakes: Any) -> Iterator[endpoint_caller.EndpointCaller]:
    waits: list[float] = []
    c = endpoint_caller.EndpointCaller(ORIGIN + "/v1", "sk-unit-key-123456", sleep=waits.append)
    c.waits = waits  # type: ignore[attr-defined]
    yield c
    c.close()


@pytest.fixture
def tei_caller(fakes: Any) -> Iterator[endpoint_caller.EndpointCaller]:
    c = endpoint_caller.EndpointCaller(TEI_ORIGIN, None, sleep=lambda s: None)
    yield c
    c.close()
