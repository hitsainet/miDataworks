"""``GET /api/v1/code-rules/by-hash/{sha}``: the shape miStudio 040 S4 reads (FTDD 040 C.5;
018 FTID §10; FTASKS 10.8). miStudio fetches a referenced code rule once at write time, recomputes
the hash from ``canonical_utf8`` and refuses a mismatch, so the keys and the exactness of the text
are a contract: a renamed key, or a text that is not the hashed bytes, breaks a consumer silently.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator

import httpx
import pytest

from src.core.config import get_settings
from tests.support import code_rule_fixtures as fx
from tests.support.fake_runner import FakeRunner, installed

#: The keys a consumer reads (miStudio 040 S4); more keys may be added, none renamed or removed.
CONSUMED = {"sha256", "document", "canonical_utf8", "cases"}


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeRunner]:
    monkeypatch.setattr(get_settings(), "code_rules_enabled", True)
    with installed(FakeRunner(fx.twins_by_hash())) as runner:
        yield runner


async def test_the_by_hash_shape_is_what_a_consumer_reads(
    client: httpx.AsyncClient, operator_name: str, fake: FakeRunner
) -> None:
    body = fx.save_body("fixture-gamma")
    assert (await client.post("/api/v1/code-rules", json=body)).status_code == 201
    sha = hashlib.sha256(
        json.dumps(
            body["document"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()  # the contract's hash rule, written out independently of the code under test
    response = await client.get(f"/api/v1/code-rules/by-hash/{sha}")
    assert response.status_code == 200, response.text
    out = response.json()
    assert CONSUMED <= set(out)
    assert out["sha256"] == sha
    assert hashlib.sha256(out["canonical_utf8"].encode("utf-8")).hexdigest() == sha
    assert json.loads(out["canonical_utf8"]) == out["document"] == body["document"]
    assert out["cases"] == body["cases"]
