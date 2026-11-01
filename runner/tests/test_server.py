"""The HTTP surface's named refusals and health (FTASKS 4.6). In-process via Starlette TestClient."""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from src.runner.config import RunnerConfig
from src.runner.server import create_app
from src.runner_contract.hashing import canonical_document_bytes, code_sha256

PACKAGED = (
    Path(__file__).resolve().parents[2]
    / "backend"
    / "src"
    / "schemas"
    / "data"
    / "midataworks-runner-v1.json"
)
FLOOR = json.loads(PACKAGED.read_text(encoding="utf-8"))["limits_floor"]
DOC = {
    "runner_contract": "midataworks.runner/v1",
    "source": "def f(text, params):\n    return 0\n",
    "functions": [{"name": "f", "output": {"type": "count"}}],
    "params_schema": {"type": "object"},
    "derived_from": {"sources": [], "statement": "x"},
}
SHA = code_sha256(DOC)
UTF8 = canonical_document_bytes(DOC).decode("utf-8")


@pytest.fixture(scope="module")
def client(bundle_dir: Path) -> TestClient:
    cfg = RunnerConfig.from_env(
        {"RUNNER_BUNDLE_DIR": str(bundle_dir), "RUNNER_CONTRACT_PATH": str(PACKAGED)}
    )
    return TestClient(create_app(cfg))


def _limits(**over: int) -> dict[str, int]:
    base = {
        "call_fuel": FLOOR["call_fuel"],
        "load_fuel": FLOOR["load_fuel"],
        "prelude_fuel": FLOOR["prelude_fuel"],
        "memory_bytes": FLOOR["memory_bytes"],
        "input_code_points": FLOOR["input_code_points"],
        "output_bytes": FLOOR["output_bytes"],
        "wall_ms": 10_000,
    }
    base.update(over)
    return base


def _run(client: TestClient, **over: Any) -> Any:
    body = {
        "contract": "midataworks.runner/v1",
        "mode": "stored",
        "lane": "batch",
        "limits": _limits(),
        "document": {"code_sha256": SHA, "canonical_utf8": UTF8},
        "calls": [{"function": "f", "params": {}, "texts": ["x"], "repeat": 1}],
    }
    body.update(over)
    return client.post("/v1/run", json=body)


def test_health_is_ok_and_lists_env_names(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["contract"] == "midataworks.runner/v1"
    assert isinstance(body["env_names"], list)
    assert "lanes" in body and set(body["lanes"]) == {"interactive", "batch"}


def test_a_good_run_answers_a_count(client: TestClient) -> None:
    assert _run(client).json()["results"][0][0] == {"count": 0}


def test_unknown_contract_is_refused(client: TestClient) -> None:
    r = _run(client, contract="midataworks.runner/v2")
    assert r.status_code == 422 and r.json()["error"]["code"] == "runner_contract_unknown"


def test_a_limit_below_floor_is_refused(client: TestClient) -> None:
    r = _run(client, limits=_limits(call_fuel=1))
    assert r.status_code == 422 and r.json()["error"]["code"] == "limit_below_floor"


def test_a_limit_above_ceiling_is_refused(client: TestClient) -> None:
    r = _run(client, limits=_limits(call_fuel=1 << 60))
    assert r.status_code == 422 and r.json()["error"]["code"] == "limit_above_ceiling"


def test_a_document_hash_mismatch_is_refused(client: TestClient) -> None:
    r = _run(client, document={"code_sha256": "0" * 64, "canonical_utf8": UTF8})
    assert r.status_code == 422 and r.json()["error"]["code"] == "document_hash_mismatch"


def test_an_uncached_document_is_409(client: TestClient) -> None:
    r = _run(client, document={"code_sha256": "a" * 64})
    assert r.status_code == 409 and r.json()["error"]["code"] == "document_not_cached"


def test_a_lone_surrogate_text_is_refused(client: TestClient) -> None:
    # A lone surrogate reaches the runner only as the JSON escape \ud800 (the caller is meant to
    # replace it with U+FFFD first); json.loads rebuilds the surrogate and units() refuses it.
    raw = json.dumps(
        {
            "contract": "midataworks.runner/v1",
            "mode": "stored",
            "lane": "batch",
            "limits": _limits(),
            "document": {"code_sha256": SHA, "canonical_utf8": UTF8},
            "calls": [
                {
                    "function": "f",
                    "params": {},
                    "texts": ["pre \ud800 post"],
                    "repeat": 1,
                }
            ],
        },
        ensure_ascii=True,
    ).encode("ascii")
    r = client.post("/v1/run", content=raw, headers={"content-type": "application/json"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "text_invalid"


def test_a_function_not_declared_is_refused(client: TestClient) -> None:
    body = {
        "contract": "midataworks.runner/v1",
        "mode": "stored",
        "lane": "batch",
        "limits": _limits(),
        "document": {"code_sha256": SHA, "canonical_utf8": UTF8},
        "calls": [{"function": "missing", "params": {}, "texts": ["x"], "repeat": 1}],
    }
    r = client.post("/v1/run", json=body)
    assert r.status_code == 422 and r.json()["error"]["code"] == "function_not_declared"


def test_the_cache_lets_a_second_call_omit_the_bytes(client: TestClient) -> None:
    assert _run(client).status_code == 200  # caches SHA
    body = {
        "contract": "midataworks.runner/v1",
        "mode": "stored",
        "lane": "batch",
        "limits": _limits(),
        "document": {"code_sha256": SHA},
        "calls": [{"function": "f", "params": {}, "texts": ["x"], "repeat": 1}],
    }
    assert client.post("/v1/run", json=body).json()["results"][0][0] == {"count": 0}


def test_dry_run_carries_fuel_used_and_diagnostic_but_stored_does_not(
    client: TestClient,
) -> None:
    raising = {
        "runner_contract": "midataworks.runner/v1",
        "source": "def f(text, params):\n    raise ValueError('boom')\n",
        "functions": [{"name": "f", "output": {"type": "count"}}],
        "params_schema": {"type": "object"},
        "derived_from": {"sources": [], "statement": "x"},
    }
    sha, utf8 = code_sha256(raising), canonical_document_bytes(raising).decode("utf-8")

    def call(mode: str) -> dict:
        body = {
            "contract": "midataworks.runner/v1",
            "mode": mode,
            "lane": "interactive",
            "limits": _limits(),
            "document": {"code_sha256": sha, "canonical_utf8": utf8},
            "calls": [{"function": "f", "params": {}, "texts": ["x"], "repeat": 1}],
        }
        return client.post("/v1/run", json=body).json()["results"][0][0]

    dry = call("dry_run")
    stored = call("stored")
    assert dry["reason"] == "rule_error" and "diagnostic" in dry and "fuel_used" in dry
    assert stored == {"reason": "rule_error"}


# --- Review R1: refusals and the lane backstop that no test pinned -------------------------------


def _doc_body(document: dict[str, Any], raw: str | None = None) -> dict[str, Any]:
    utf8 = raw if raw is not None else canonical_document_bytes(document).decode("utf-8")
    sha = hashlib.sha256(utf8.encode("utf-8")).hexdigest()
    return {"code_sha256": sha, "canonical_utf8": utf8}


def test_a_document_that_is_not_the_canonical_bytes_is_refused(client: TestClient) -> None:
    """The bytes hash to the sha named, but they are not the canonical JSON: refused, never run."""
    spaced = json.dumps(DOC, indent=1, sort_keys=True)
    r = _run(client, document=_doc_body(DOC, spaced))
    assert r.status_code == 422 and r.json()["error"]["code"] == "document_invalid"


def test_a_document_of_another_major_is_refused(client: TestClient) -> None:
    """The request says v1, the document says v2 (and hashes correctly): refused (FR-018.9)."""
    v2 = {**DOC, "runner_contract": "midataworks.runner/v2"}
    r = _run(client, document=_doc_body(v2))
    assert r.status_code == 422 and r.json()["error"]["code"] == "runner_contract_unknown"


@pytest.mark.parametrize("name", ["call_fuel", "wall_ms"])
def test_a_bool_is_not_a_limit(client: TestClient, name: str) -> None:
    r = _run(client, limits=_limits(**{name: True}))
    assert r.status_code == 422 and r.json()["error"]["code"] == "request_invalid"


def test_a_bool_is_not_a_repeat(client: TestClient) -> None:
    calls = [{"function": "f", "params": {}, "texts": ["x"], "repeat": True}]
    r = _run(client, calls=calls)
    assert r.status_code == 422 and r.json()["error"]["code"] == "request_invalid"


def test_a_text_of_exactly_the_input_limit_runs(client: TestClient) -> None:
    """The input limit counts code points and is inclusive: 65,536 four-byte code points run; one
    more is rule_input_too_large (the over case is the cases file's)."""
    limit = FLOOR["input_code_points"]
    calls = [
        {"function": "f", "params": {}, "texts": ["\U0001f600" * limit], "repeat": 1},
        {"function": "f", "params": {}, "texts": ["\U0001f600" * (limit + 1)], "repeat": 1},
    ]
    results = _run(client, calls=calls).json()["results"]
    assert results == [[{"count": 0}], [{"reason": "rule_input_too_large"}]]


def test_a_body_over_the_bound_is_413(bundle_dir: Path) -> None:
    cfg = RunnerConfig.from_env(
        {
            "RUNNER_BUNDLE_DIR": str(bundle_dir),
            "RUNNER_CONTRACT_PATH": str(PACKAGED),
            "RUNNER_BATCH_TEXTS": "1",
            "RUNNER_MAX_SOURCE_BYTES": "1024",
        }
    )
    small = TestClient(create_app(cfg))
    bound = 1 * cfg.max_input_code_points * 4 + 2 * 1024 + 65_536
    r = small.post(
        "/v1/run", content=b" " * (bound + 1), headers={"content-type": "application/json"}
    )
    assert r.status_code == 413 and r.json()["error"]["code"] == "body_too_large"


def test_a_full_lane_past_the_backstop_answers_worker_unavailable(client: TestClient) -> None:
    """FTASKS 4.6: a unit that cannot get a slot within the request's wall time does not run and
    answers rule_worker_unavailable; it never waits forever behind a long build."""
    from src.runner.lanes import Lane

    runner = client.app.state.runner  # type: ignore[attr-defined]
    lane = Lane("test", 1)
    gate = threading.Event()
    blocker = lane.submit(gate.wait, 1.0)
    try:
        doc = runner.document({"code_sha256": SHA, "canonical_utf8": UTF8})
        units = runner.units([{"function": "f", "params": {}, "texts": ["x"]}], doc)
        limits = runner.limits(_limits(wall_ms=100))
        out: list[Any] = []
        worker = threading.Thread(
            target=lambda: out.append(runner.execute(doc, units, limits, "stored", lane)),
            daemon=True,
        )
        worker.start()
        worker.join(timeout=5)
        stuck = worker.is_alive()
        gate.set()  # a lane that waited forever is released here, so the test ends either way
        worker.join(timeout=5)
        assert not stuck, "the unit waited for a slot past the request's wall time"
        assert out == [[[{"reason": "rule_worker_unavailable"}]]]
    finally:
        gate.set()
        blocker.result(timeout=5)
        lane.shutdown()


def test_a_chunked_body_is_bounded_while_it_is_read() -> None:
    """Review R1 L6: a body with no Content-Length (chunked) is refused once it passes the bound,
    having read only about the bound, never the whole body first."""
    import anyio

    from src.runner.server import Refusal, read_bounded

    consumed: list[int] = []

    async def endless() -> Any:
        for _ in range(10_000):  # 640 MB if read whole
            consumed.append(1)
            yield b" " * 65_536

    async def read() -> None:
        await read_bounded(endless(), 200_000)

    with pytest.raises(Refusal) as caught:
        anyio.run(read)
    assert caught.value.code == "body_too_large" and caught.value.status == 413
    assert len(consumed) == 4, "stopped at the first chunk past 200,000 bytes"

    async def small() -> Any:
        yield b'{"a":'
        yield b" 1}"

    async def read_small() -> bytes:
        return await read_bounded(small(), 200_000)

    assert anyio.run(read_small) == b'{"a": 1}'


def test_a_chunked_body_over_the_bound_is_413_over_http(bundle_dir: Path) -> None:
    cfg = RunnerConfig.from_env(
        {
            "RUNNER_BUNDLE_DIR": str(bundle_dir),
            "RUNNER_CONTRACT_PATH": str(PACKAGED),
            "RUNNER_BATCH_TEXTS": "1",
            "RUNNER_MAX_SOURCE_BYTES": "1024",
        }
    )
    small = TestClient(create_app(cfg))
    bound = 1 * cfg.max_input_code_points * 4 + 2 * 1024 + 65_536

    def chunks() -> Any:
        for _ in range(bound // 65_536 + 2):
            yield b" " * 65_536

    r = small.post("/v1/run", content=chunks(), headers={"content-type": "application/json"})
    assert r.status_code == 413 and r.json()["error"]["code"] == "body_too_large"
