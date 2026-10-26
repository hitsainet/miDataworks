"""``dedup_embedding`` and the embedding service (004 FTASKS 15.1 - 15.4, 15.6; suite tracker 4.10).

CPU only: the embeddings endpoint is a fake OpenAI-compatible server behind 005's real
``EndpointCaller`` (``install_transport``), so headers, batching, backpressure and errors run the
production path. The step runs through 003's real executor (``run_operator``), which resolves the
``embeddings`` role and takes the lease through the ports. The live check against a real embedding
model is listed for the GPU block (004 FTASKS 15.0).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest

from src.clients import endpoint_caller
from src.operators import endpoint_port
from src.operators.errors import OperatorError, StepFailed
from tests.fixtures.humor_pool import with_system_columns
from tests.support.curation_fixtures import run_operator

ROLES = {"text": "content", "note": "metadata"}
KEY = "sk-embed-test-0123456789"
BASE = "http://embed.test/v1"


def vector_for(text: str) -> list[float]:
    """Texts sharing their first word are near-identical; others are orthogonal-ish."""
    head = text.split()[0] if text.split() else ""
    base = [0.0] * 8
    base[sum(map(ord, head)) % 8] = 1.0
    tweak = (sum(map(ord, text)) % 7) / 1000.0
    return [b + tweak for b in base]


@dataclass
class FakeEmbeddings:
    calls: list[dict[str, Any]] = field(default_factory=list)
    script: list[httpx.Response] = field(default_factory=list)

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls.append(
            {"path": request.url.path, "headers": dict(request.headers), "body": body}
        )
        if self.script:
            return self.script.pop(0)
        for i, t in enumerate(
            body["input"]
        ):  # what the real servers answer (production 2026-10-17)
            if not str(t).strip():
                return httpx.Response(
                    400,
                    json={
                        "error": {
                            "code": "invalid_parameter",
                            "message": f"input[{i}] must not be empty",
                        }
                    },
                )
        data = [
            {"index": i, "embedding": vector_for(t), "object": "embedding"}
            for i, t in enumerate(body["input"])
        ]
        return httpx.Response(200, json={"data": data, "model": body["model"]})


class Resolver:
    def __init__(
        self, model: str | None = "embed-1", key_origin: str | None = "http://embed.test:80"
    ) -> None:
        self.model = model
        self.key_origin = key_origin

    def resolve(self, role: str) -> endpoint_port.ResolvedEndpoint:
        assert role == "embeddings"
        return endpoint_port.ResolvedEndpoint(
            role=role,
            base_url=BASE,
            model=self.model,
            api_key=KEY,
            is_millm=True,
            key_origin=self.key_origin,
        )


class Leases:
    def __init__(self) -> None:
        self.acquired = 0
        self.released = 0

    def acquire(self, endpoint: endpoint_port.ResolvedEndpoint) -> endpoint_port.Lease:
        self.acquired += 1
        return endpoint_port.Lease(model=str(endpoint.model), lease_id="lease-xyz")

    def renew(self, lease: endpoint_port.Lease) -> None:
        return None

    def release(self, lease: endpoint_port.Lease) -> None:
        self.released += 1


@pytest.fixture
def fake(data_dir: Path) -> Iterator[FakeEmbeddings]:
    server = FakeEmbeddings()
    previous = endpoint_caller.install_transport(lambda origin: httpx.MockTransport(server.handle))
    old_resolver = endpoint_port.install_resolver(Resolver())
    yield server
    endpoint_caller.install_transport(previous)
    endpoint_port.install_resolver(old_resolver)


@pytest.fixture
def leases() -> Iterator[Leases]:
    manager = Leases()
    old = endpoint_port.install_lease_manager(manager)
    yield manager
    endpoint_port.install_lease_manager(old)


def table(texts: list[str], splits: list[str] | None = None) -> Any:
    rows = [{"text": t, "note": None} for t in texts]
    t = with_system_columns(rows)
    if splits is not None:
        import pyarrow as pa

        t = t.set_column(t.schema.get_field_index("_dw_split"), "_dw_split", pa.array(splits))
    return t


def dedup(params: dict[str, Any], data: Any) -> tuple[Any, dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    ran = run_operator("dedup_embedding", params, data, ROLES, report_sink=reports)
    assert len(reports) == 1
    return ran, reports[0]


TEXTS = [
    "apple pie recipe one",
    "apple pie recipe two",
    "banana bread loaf",
    "cherry tart small",
    "apple pie recipe three",
]


def test_near_duplicates_are_dropped_naming_the_kept_row_and_the_cosine(
    fake: FakeEmbeddings, leases: Leases
) -> None:
    ran, report = dedup({"threshold": 0.99}, table(TEXTS))
    dropped = ran.events_by_reason("near_duplicate_embedding")
    assert len(dropped) == 2
    kept_texts = set(ran.output.column("text").to_pylist())
    assert {"banana bread loaf", "cherry tart small"} <= kept_texts
    assert sum(1 for t in kept_texts if t.startswith("apple")) == 1
    for e in dropped:
        assert e["statistic_name"] == "cosine" and e["statistic_value"] >= 0.99
        assert json.loads(e["threshold"]) == {"value": 0.99, "comparator": ">="}
    assert report["basis"] == "embedding" and report["pinned"] is True
    assert report["embedding"]["model"] == "embed-1"
    assert KEY not in json.dumps(ran.meta) and KEY not in json.dumps(ran.events.to_pylist())


def test_the_request_is_strict_refuse_load_leased_and_keyed_in_one_batch(
    fake: FakeEmbeddings, leases: Leases
) -> None:
    run_operator("dedup_embedding", {"threshold": 0.99}, table(TEXTS), ROLES)
    assert len(fake.calls) == 1 and leases.acquired == 1 and leases.released == 1
    call = fake.calls[0]
    assert call["path"] == "/v1/embeddings"
    headers = {k.lower(): v for k, v in call["headers"].items()}
    assert headers["x-millm-strict"] == "true"
    assert headers["x-millm-load-policy"] == "refuse"
    assert headers["x-millm-lease"] == "lease-xyz"
    assert headers["authorization"] == f"Bearer {KEY}"
    assert call["body"]["model"] == "embed-1" and len(call["body"]["input"]) == len(TEXTS)


def test_a_key_bound_to_another_origin_is_not_sent(fake: FakeEmbeddings, leases: Leases) -> None:
    """2026-10-20: the embedding call sends the role key only to the origin it was entered for."""
    old = endpoint_port.install_resolver(Resolver(key_origin="http://elsewhere.test:80"))
    try:
        run_operator("dedup_embedding", {"threshold": 0.99}, table(TEXTS), ROLES)
    finally:
        endpoint_port.install_resolver(old)
    assert len(fake.calls) == 1
    assert "authorization" not in {k.lower() for k in fake.calls[0]["headers"]}


def test_a_second_run_reads_the_cache_and_calls_nothing(
    fake: FakeEmbeddings, leases: Leases
) -> None:
    run_operator("dedup_embedding", {"threshold": 0.99}, table(TEXTS), ROLES)
    _, report = dedup({"threshold": 0.99}, table(TEXTS))
    assert len(fake.calls) == 1
    assert report["rows_from_cache"] == len(TEXTS) and report["requests"] == 0


def test_a_row_over_the_input_cap_is_skipped_never_truncated(
    fake: FakeEmbeddings, leases: Leases, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.core.config import get_settings

    monkeypatch.setattr(get_settings(), "curation_embed_max_input_chars", 25)
    long_text = "apple pie recipe one but much much longer than the cap"
    ran, report = dedup({"threshold": 0.99}, table([*TEXTS, long_text]))
    sent = [t for c in fake.calls for t in c["body"]["input"]]
    assert sorted(sent) == sorted(TEXTS)  # the long row is not sent, cut or otherwise
    assert long_text in ran.output.column("text").to_pylist()
    assert report["rows_skipped"] == {"over_input_cap": 1, "empty_input": 0}


def test_an_empty_row_is_skipped_never_sent(fake: FakeEmbeddings, leases: Leases) -> None:
    """Round 2 item 1b: an embeddings endpoint answers ``400 input[0] must not be empty`` and the
    whole batch fails. Blank rows are never sent; they stay in the output, compared with nothing."""
    ran, report = dedup({"threshold": 0.99}, table([*TEXTS, "", "   "]))
    sent = [t for c in fake.calls for t in c["body"]["input"]]
    assert sent and all(t.strip() for t in sent)
    assert sorted(sent) == sorted(TEXTS)
    assert ran.output.column("text").to_pylist().count("") == 1
    assert report["rows_skipped"] == {"over_input_cap": 0, "empty_input": 2}


def test_a_503_with_retry_after_is_retried(fake: FakeEmbeddings, leases: Leases) -> None:
    fake.script = [
        httpx.Response(503, headers={"Retry-After": "0"}, json={"error": {"code": "busy"}})
    ]
    ran, _ = dedup({"threshold": 0.99}, table(TEXTS))
    assert len(fake.calls) == 2
    assert len(ran.events_by_reason("near_duplicate_embedding")) == 2


def test_an_unconfigured_role_is_refused_naming_settings(data_dir: Path) -> None:
    old = endpoint_port.install_resolver(endpoint_port.UnconfiguredResolver())
    try:
        with pytest.raises((StepFailed, OperatorError)) as exc:
            run_operator("dedup_embedding", {}, table(TEXTS), ROLES)
    finally:
        endpoint_port.install_resolver(old)
    assert "Settings" in str(exc.value) and "embeddings" in str(exc.value)


def test_more_rows_than_the_exact_cap_are_refused(
    fake: FakeEmbeddings, leases: Leases, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.core.config import get_settings

    monkeypatch.setattr(get_settings(), "curation_embed_exact_max_rows", 3)
    with pytest.raises((StepFailed, OperatorError)) as exc:
        run_operator("dedup_embedding", {}, table(TEXTS), ROLES)
    assert "at most 3" in str(exc.value)
    assert fake.calls == []


def test_a_group_spanning_splits_is_reported_not_dropped(
    fake: FakeEmbeddings, leases: Leases
) -> None:
    splits = ["train", "test", "train", "train", "train"]
    ran, report = dedup({"threshold": 0.99}, table(TEXTS, splits))
    assert ran.events_by_reason("near_duplicate_embedding") == []
    assert report["cross_split_group_count"] == 1
