"""Public values only, and no behaviour names, in the cases file (FR-018.13, FR-018.47; FTASKS 3.9)."""

from __future__ import annotations

import json

from src.rules import public_values
from src.runner_contract import cases as cases_mod
from src.runner_contract.cases import PACKAGED_CASES_PATH

CASES = cases_mod.load_cases()


def test_the_cases_file_carries_only_public_values() -> None:
    assert public_values.string_violations(CASES) == []


def test_a_nonpublic_valid_value_in_a_case_is_a_violation() -> None:
    polluted = json.loads(json.dumps(CASES))
    polluted["conformance"][0]["text"] = "card 4111 1111 1111 1111 here"
    found = public_values.string_violations(polluted)
    assert found and "card_pan" in found[0]


def test_no_behaviour_key_in_the_cases_file() -> None:
    assert "behaviour_key" not in PACKAGED_CASES_PATH.read_text(encoding="utf-8")
