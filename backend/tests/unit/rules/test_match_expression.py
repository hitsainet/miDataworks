"""D53: ``rule_set.match_of`` - the positive a consumer reads, explicit or inferred and flagged."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from src.rules import rule_set

PACK = Path(__file__).resolve().parents[4] / "behaviours" / "dlp-ssn-us" / "rule_set.json"


def document(**over: Any) -> rule_set.RuleSetDocument:
    data: dict[str, Any] = copy.deepcopy(json.loads(PACK.read_text(encoding="utf-8")))
    data["cases"] = []
    data.update(over)
    return rule_set.parse(data)


def test_explicit_match_is_read_as_stated() -> None:
    found = rule_set.match_of(document(match={"version": 1, "any_of": ["cued"]}))
    assert found == {"source": "explicit", "version": 1, "any_of": ["cued"], "flags": []}


def test_without_match_every_fired_output_is_the_positive() -> None:
    found = rule_set.match_of(document())
    assert found["source"] == "inferred" and found["version"] is None
    assert found["any_of"] == ["valid_ssn", "cued"]
    assert found["flags"] == []


def test_an_inference_reading_a_veto_condition_is_flagged() -> None:
    """R1-F3: a veto condition exposed as a fired output made the veto never hold."""
    raw = json.loads(PACK.read_text(encoding="utf-8"))
    outputs = [
        *raw["outputs"],
        {"column": "ssn_masked", "type": "bool", "value": {"fired": "is_masked"}},
    ]
    found = rule_set.match_of(document(outputs=outputs))
    assert found["any_of"] == ["valid_ssn", "cued", "is_masked"]
    assert found["flags"] == [rule_set.MATCH_INCLUDES_VETO_CONDITION]
    # Stated explicitly, the same document carries no flag: the positive is what it says.
    explicit = rule_set.match_of(
        document(outputs=outputs, match={"version": 1, "any_of": ["cued"]})
    )
    assert explicit["flags"] == []
