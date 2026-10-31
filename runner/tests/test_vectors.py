"""Every vector of runner-cases-v1.json through the REAL runner over HTTP (FR-018.10, FR-018.11; FTASKS 4.9).

Conformance, the non-escape hostile classes, hash (re-hashed here), imports and limits all run
against the subprocess runner started with no database and no secret. The escape-form hostile
vectors (classes 1-4, 9) and the guest-source marker/forge vectors (17-19) are the operator's open
item (``coverage.PENDING_HOSTILE_CLASSES``); the marker protocol is proven in ``test_host.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.runner_contract.cases import load_cases
from src.runner_contract.hashing import canonical_document_bytes, code_sha256
from src.runner_contract.text import replace_lone_surrogates

CASES = load_cases()
DOCUMENTS = CASES["documents"]
PACKAGED = (
    Path(__file__).resolve().parents[2]
    / "backend"
    / "src"
    / "schemas"
    / "data"
    / "midataworks-runner-v1.json"
)
FLOOR = json.loads(PACKAGED.read_text(encoding="utf-8"))["limits_floor"]


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


def _body(
    doc_id: str,
    function: str,
    params: dict,
    texts: list[str],
    limits: dict,
    repeat: int = 1,
    mode: str = "stored",
) -> dict[str, Any]:
    d = DOCUMENTS[doc_id]
    return {
        "contract": "midataworks.runner/v1",
        "mode": mode,
        "lane": "batch",
        "limits": limits,
        "document": {
            "code_sha256": code_sha256(d),
            "canonical_utf8": canonical_document_bytes(d).decode("utf-8"),
        },
        "calls": [{"function": function, "params": params, "texts": texts, "repeat": repeat}],
    }


def _result(runner_process: Any, body: dict[str, Any]) -> dict[str, Any]:
    r = runner_process.run(body)
    assert r.status_code == 200, r.text
    return r.json()["results"][0][0]


def _match(expect: dict, got: dict) -> bool:
    if "count" in expect:
        return got.get("count") == expect["count"]
    if "reason" in expect:
        return got.get("reason") == expect["reason"]
    return False


CONF_SINGLE = [c for c in CASES["conformance"] if "calls" not in c]


@pytest.mark.parametrize("case", CONF_SINGLE, ids=[c["id"] for c in CONF_SINGLE])
def test_conformance(runner_process: Any, case: dict) -> None:
    text = replace_lone_surrogates(case["text"])
    got = _result(
        runner_process,
        _body(case["doc"], case["function"], case["params"], [text], _limits()),
    )
    assert _match(case["expect"], got), got


def test_fresh_instance_counts(runner_process: Any) -> None:
    case = next(c for c in CASES["conformance"] if c["id"] == "conf.fresh_instance")
    body = _body(
        case["doc"],
        case["function"],
        case["params"],
        [case["text"]],
        _limits(),
        repeat=1,
    )
    body["calls"] = body["calls"] * 3
    r = runner_process.run(body)
    counts = [row[0].get("count") for row in r.json()["results"]]
    assert counts == case["expect"]["counts"]


@pytest.mark.parametrize("case", CASES["hostile"], ids=[c["id"] for c in CASES["hostile"]])
def test_hostile(runner_process: Any, case: dict) -> None:
    got = _result(
        runner_process,
        _body(
            case["doc"],
            case["function"],
            case["params"],
            [replace_lone_surrogates(case["text"])],
            _limits(),
        ),
    )
    assert _match(case["expect"], got), got


@pytest.mark.parametrize("case", CASES["imports"], ids=[c["id"] for c in CASES["imports"]])
def test_imports(runner_process: Any, case: dict) -> None:
    got = _result(runner_process, _body(case["doc"], "f", {}, ["a a a"], _limits()))
    assert _match(case["expect"], got), got


@pytest.mark.parametrize("case", CASES["limits"], ids=[c["id"] for c in CASES["limits"]])
def test_limits(runner_process: Any, case: dict) -> None:
    limits = _limits()
    text = case["text"]
    if case["id"] == "limit.wall_ms.timeout":
        limits = _limits(call_fuel=FLOOR["call_fuel"], wall_ms=50)
    if case["id"] == "limit.input_code_points.exceed":
        text = "x" * (FLOOR["input_code_points"] + 1)
    got = _result(
        runner_process,
        _body(case["doc"], case["function"], case["params"], [text], limits),
    )
    assert _match(case["expect"], got), got


@pytest.mark.parametrize("case", CASES["hash"], ids=[c["id"] for c in CASES["hash"]])
def test_hash(case: dict) -> None:
    d = DOCUMENTS[case["document"]]
    assert canonical_document_bytes(d).decode("utf-8") == case["canonical_utf8"]
    assert code_sha256(d) == case["sha256"]


def test_all_hostile_vectors_pass(runner_process: Any) -> None:
    """Every hostile vector in the file passes. The file holds classes 5-8 and 10-16 only: the
    escape-form classes (1-4, 9) and the guest-source forms of 17-19 are
    ``coverage.PENDING_HOSTILE_CLASSES``, NOT covered here (the host defences they would exercise
    are pinned at host level by ``test_wasi_table.py``)."""
    from src.runner_contract.coverage import PENDING_HOSTILE_CLASSES

    present = {c["number"] for c in CASES["hostile"]}
    assert present == set(range(1, 20)) - PENDING_HOSTILE_CLASSES
    for case in CASES["hostile"]:
        got = _result(
            runner_process,
            _body(
                case["doc"],
                case["function"],
                case["params"],
                [replace_lone_surrogates(case["text"])],
                _limits(),
            ),
        )
        assert _match(case["expect"], got), (case["id"], got)


def test_inspect(runner_process: Any) -> None:
    d = DOCUMENTS["two_functions"]
    r = runner_process.inspect(
        {
            "contract": "midataworks.runner/v1",
            "document": {
                "code_sha256": code_sha256(d),
                "canonical_utf8": canonical_document_bytes(d).decode("utf-8"),
            },
        }
    )
    assert r.json() == {"functions": ["a", "b"], "imports": []}
