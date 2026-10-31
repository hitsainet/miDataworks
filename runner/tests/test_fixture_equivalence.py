"""Fixture 1 reproduces the validator library's Luhn verdicts through the REAL runner (feature 018;
FTASKS 14.6; FR-018.45; FTDD §11.6).

Fixture 1 (``fixture1_luhn`` in ``runner-cases-v1.json``) re-implements the Luhn check digit as a code
rule. The external truth is 016's published Luhn vectors (``rule-signal-cases-v1.json``,
``validators["luhn@1"]``): for a value case the rule counts 1 exactly when the vector says the value
is valid, for a scan case it counts the vector's valid candidates. Every vector, through the runner
started with no database and no secret; the backend's twin is held to the same vectors against the
library itself (``backend/tests/unit/test_code_rule_fixtures.py``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.runner_contract.cases import load_cases
from src.runner_contract.hashing import canonical_document_bytes, code_sha256

RULE_CASES = Path(__file__).resolve().parents[2] / "docs" / "schemas" / "rule-signal-cases-v1.json"
LUHN = json.loads(RULE_CASES.read_text(encoding="utf-8"))["validators"]["luhn@1"]["cases"]
DOCUMENT = load_cases()["documents"]["fixture1_luhn"]
FLOOR = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "backend"
        / "src"
        / "schemas"
        / "data"
        / "midataworks-runner-v1.json"
    ).read_text(encoding="utf-8")
)["limits_floor"]


def text_and_expected(case: dict[str, Any]) -> tuple[str, int]:
    """The text a code rule reads, and the count the vector's verdict means."""
    inp, expect = case["input"], case["expect"]
    if "text" in inp:
        return inp["text"], sum(1 for c in expect["candidates"] if c["valid"])
    return inp.get("before", "") + inp["value"] + inp.get("after", ""), int(expect["valid"])


def test_there_are_luhn_vectors_of_both_kinds() -> None:
    kinds = {"text" in c["input"] for c in LUHN}
    assert kinds == {True, False} and any(text_and_expected(c)[1] for c in LUHN)


@pytest.mark.parametrize("case", LUHN, ids=[c["id"] for c in LUHN])
def test_fixture_one_counts_what_the_luhn_vector_says(runner_process: Any, case: dict) -> None:
    text, expected = text_and_expected(case)
    body = {
        "contract": "midataworks.runner/v1",
        "mode": "stored",
        "lane": "batch",
        "limits": {
            **{k: FLOOR[k] for k in ("call_fuel", "load_fuel", "prelude_fuel")},
            **{k: FLOOR[k] for k in ("memory_bytes", "input_code_points", "output_bytes")},
            "wall_ms": 10_000,
        },
        "document": {
            "code_sha256": code_sha256(DOCUMENT),
            "canonical_utf8": canonical_document_bytes(DOCUMENT).decode("utf-8"),
        },
        "calls": [{"function": "check", "params": {}, "texts": [text], "repeat": 1}],
    }
    response = runner_process.run(body)
    assert response.status_code == 200, response.text
    assert response.json()["results"][0][0] == {"count": expected}, case["id"]
