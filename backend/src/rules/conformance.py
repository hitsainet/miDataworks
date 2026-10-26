"""Which production operators conform to the contract, and on which cases (FR-016.12; OQ-3, OQ-4).

``CONFORMANT`` maps an operator ref to the scope of cases its REAL ``run`` must pass
(``tests/unit/rules/test_rule_signal_conformance.py`` runs each on a pyarrow batch built from the
vectors). ``NOT_CONFORMANT`` maps a ref to the reason it is not held to the contract; that reason
is shown beside the move to the conformant version in ``GET /operators`` and the upgrade plan.
Declaring ``regex_match@1`` conformant turns the conformance run red.

The scope predicates read case data only, so this module stays pure.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

FORMAT_VALIDATORS = ("card_pan", "ssn_us", "aba_routing", "iban", "nhs", "aadhaar")


@dataclass(frozen=True)
class Scope:
    #: What part of the contract the operator is held to, in words.
    description: str
    #: The ``rule-signal-cases-v1.json`` section the cases come from.
    section: str
    #: Which cases of that section are in scope (case, its document or None).
    applies: Callable[[dict[str, Any], dict[str, Any] | None], bool]


def _single_pattern_over_text(case: dict[str, Any], document: dict[str, Any] | None) -> bool:
    if document is None or "text" not in case["input"]:
        return False
    signals = document["signals"]
    return (
        len(signals) == 1
        and signals[0]["kind"] == "pattern"
        and signals[0]["reads"] == "text"
        and any(e == {"signal": signals[0]["id"]} for e in document["expressions"].values())
    )


def _format_vector(case: dict[str, Any], _: dict[str, Any] | None) -> bool:
    return case["validator"].split("@")[0] in FORMAT_VALIDATORS and case["validator"].endswith("@1")


def _phrase_proximity_v1(case: dict[str, Any], document: dict[str, Any] | None) -> bool:
    """FTID 016 I7: ``phrase_proximity@1`` measures UTF-8 bytes on either side, without word
    boundaries, case-insensitively, with no width folding, from one anchor span per row (a case
    with several anchor spans runs one row per span)."""
    if document is None or "text" not in case["input"]:
        return False
    prox = [s for s in document["signals"] if s["kind"] == "proximity"]
    if len(prox) != 1:
        return False
    p = prox[0]
    anchor = next(s for s in document["signals"] if s["id"] == p["anchor"])
    return (
        p["unit"] == "utf8_bytes"
        and p["side"] == "either"
        and p["word_boundary"] is False
        and p["ignore_case"] is True
        and p["fold_fullwidth_ascii"] is False
        and anchor["kind"] == "pattern"
        and anchor["reads"] == "text"
    )


CONFORMANT: dict[str, Scope] = {
    "regex_match@2": Scope(
        "every pattern case with one pattern signal over the text: matched, count, first, spans",
        "pattern",
        _single_pattern_over_text,
    ),
    "checksum_validate@1": Scope(
        "every case of the six format validators, mode=value (valid, reason, tier, layout, "
        "normalised, detail) and mode=scan (the first candidate of highest tier, then valid)",
        "validators",
        _format_vector,
    ),
    "phrase_proximity@1": Scope(
        "proximity cases in UTF-8 bytes on either side, without word boundaries, ignoring case, "
        "with no width folding, one row per anchor span (FTID 016 I7)",
        "proximity",
        _phrase_proximity_v1,
    ),
}

NOT_CONFORMANT: dict[str, str] = {
    "regex_match@1": (
        "character classes and engine: Python re, where \\d and \\w match non-ASCII digits and "
        "letters, \\s matches \\v, and $ matches before a final newline; move to regex_match@2 "
        "for the rule-signal dialect"
    ),
}


def conformance_for(ref: str) -> dict[str, Any] | None:
    """The ``conformance`` field ``GET /operators`` shows for an operator ref."""
    if ref in CONFORMANT:
        return {"conformant": True, "scope": CONFORMANT[ref].description}
    if ref in NOT_CONFORMANT:
        return {"conformant": False, "reason": NOT_CONFORMANT[ref]}
    return None
