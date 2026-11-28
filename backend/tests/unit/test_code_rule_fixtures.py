"""The fixture code rules hold to external truths (feature 018; FTASKS 14.6, 14.8; FR-018.45, 018.47).

Fixture 1 re-implements the Luhn check digit. Its backend twin (what the fake runner runs; the
backend never runs a source) is held here to the validator LIBRARY's verdicts on every published
``luhn@1`` vector, and the published document is the contract's conformance vector byte for byte;
the real runner is held to the same vectors in ``runner/tests/test_fixture_equivalence.py``. Fixture
2 counts tokens by digit sum, which no declarative signal can say. No fixture carries a behaviour
key, and 016's behaviour-name guard reads every module this slice added.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.rules.validators import resolve
from src.runner_contract.cases import load_cases
from src.runner_contract.hashing import code_sha256
from tests.support import code_rule_fixtures as fx

REPO = Path(__file__).resolve().parents[3]
RULE_CASES = json.loads((REPO / "docs/schemas/rule-signal-cases-v1.json").read_text("utf-8"))
LUHN = RULE_CASES["validators"]["luhn@1"]["cases"]


def library_count(case: dict[str, Any]) -> tuple[str, int]:
    validator = resolve("luhn", 1)
    inp = case["input"]
    if "text" in inp:
        return inp["text"], sum(1 for c in validator.scan(inp["text"], {}) if c.verdict.valid)
    text = inp.get("before", "") + inp["value"] + inp.get("after", "")
    valid = validator.check(inp["value"], inp.get("before", ""), inp.get("after", ""), {}).valid
    return text, int(valid)


@pytest.mark.parametrize("case", LUHN, ids=[c["id"] for c in LUHN])
def test_fixture_one_equals_the_luhn_library_on_every_vector(case: dict[str, Any]) -> None:
    text, want = library_count(case)
    assert fx.luhn_twin(text, {}) == want, case["id"]


def test_fixture_one_is_the_published_conformance_vector() -> None:
    cases = load_cases()
    vector = next(c for c in cases["conformance"] if c["id"] == "conf.fixture1_luhn")
    assert vector["doc"] == "fixture1_luhn"
    assert code_sha256(fx.luhn_document()) == code_sha256(cases["documents"]["fixture1_luhn"])
    assert fx.luhn_twin(vector["text"], vector["params"]) == vector["expect"]["count"]
    two = next(c for c in cases["conformance"] if c["id"] == "conf.fixture2_digit_sum")
    gamma = fx.fixture("fixture-gamma")["twin"]["digit_sum"]
    assert gamma(two["text"], two["params"]) == two["expect"]["count"]


def test_no_fixture_document_or_case_carries_a_behaviour_key() -> None:
    """FR-018.47: the vectors and the fixture DOCUMENTS name no behaviour (the key is the save's
    field, outside the hashed body)."""
    for name, data in fx.FIXTURES.items():
        assert "behaviour_key" not in json.dumps(data["document"]), name
        assert "behaviour_key" not in json.dumps(data["cases"]), name
    assert "behaviour_key" not in json.dumps(load_cases())
    assert "behaviour_key" not in json.dumps(fx.luhn_document())


def test_the_behaviour_name_guard_reads_every_module_this_slice_added() -> None:
    """FR-018.47 (FTASKS 14.8): 016's guard scans by area; each new S4 module is in its file set
    (fail closed: a module the scan skipped would be unguarded)."""
    from tests.unit.test_behaviour_names_absent import scanned_files

    scanned = {rel for rel, _ in scanned_files()}
    for module in (
        "backend/src/services/code_rules/resolution.py",
        "backend/src/services/code_rules/dispatch.py",
        "backend/src/services/code_rules/uses.py",
        "backend/src/services/code_rules/carry.py",
        "backend/src/operators/runner_port.py",
        "backend/src/operators/native/rules/rule_set_op.py",
        "backend/src/rules/compose.py",
    ):
        assert module in scanned, module
