"""The DLP dictionaries' labelling chain as recipe steps, and the documented examples (e2e DLP run,
2026-10-21).

Data from the consolidated DLP dictionary doc: each dictionary's High Confidence phrases (as the
e2e run read them) and its 62 documented examples (``fixtures/dlp/doc_examples.jsonl``: the
dictionary, the text before the example, the example, the text after, the tier the doc lists it
under, and whether the doc says it triggers at that tier). Nothing here re-implements a rule: the
rules are the operators'. ``hand_sample`` holds 321 rows of the run's hand-written Credit Cards
recipe output (stratified by kind and tier) WITH the labels that recipe gave them, the oracle the
new chain must reproduce byte for byte.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "dlp"

PHRASES: dict[str, list[str]] = {
    "cc": [
        "Visa",
        "MasterCard",
        "Amex",
        "Discover",
        "Diners Club",
        "JCB",
        "CVV Code",
        "CCV Number",
        "credit card number",
        "card holder name",
        "expiration date",
    ],
    "ssn": ["social security number", "date of birth", "tax payer"],
    "aba": [
        "aba routing number",
        "aba number",
        "american bank association routing number",
        "bank routing number",
        "routing transit number",
    ],
    "iban": ["IBAN", "IBAN code", "IBAN#", "International Bank Account Number", "IBAN number"],
}
SCHEME = {"cc": "card_pan", "ssn": "ssn_us", "aba": "aba_routing", "iban": "iban"}
#: The proximity the run chose (the docs make it a per-dictionary setting with no default).
WITHIN_BYTES = 100
RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}


def iban_country_phrases() -> list[str]:
    """``KEY=phrase`` entries: an IBAN's own country name is a High phrase for it."""
    from src.operators.native.identifiers.schemes import IBAN_REGISTRY

    # "Bosnia" as the run wrote it: a phrase is a substring, so it also finds the full name.
    short = {"BA": "Bosnia"}
    return [
        f"{code}={short.get(code, name)}" for code, (_, _, name) in sorted(IBAN_REGISTRY.items())
    ]


def label_steps(
    dictionary: str, *, value: str, before: str, after: str, text: str
) -> list[tuple[str, dict[str, Any]]]:
    """The whole labelling stage in four steps (the hand-written recipe took 21-38)."""
    proximity: dict[str, Any] = {
        "column": text,
        "phrases": PHRASES[dictionary],
        "max_distance_bytes": WITHIN_BYTES,
        "anchor_start_column": "cv_start",
        "anchor_end_column": "cv_end",
    }
    if dictionary == "iban":
        proximity["key_column"] = "cv_detail"
        proximity["key_phrases"] = iban_country_phrases()
    return [
        (
            "checksum_validate",
            {
                "column": value,
                "scheme": SCHEME[dictionary],
                "before_column": before,
                "after_column": after,
            },
        ),
        ("phrase_proximity", proximity),
        (
            "rule_label",
            {
                "output_column": "tier",
                "rules": ["high if cv_tier == medium and pp_near == true"],
                "default": "@cv_tier",
            },
        ),
        (
            "rule_label",
            {"output_column": "label", "rules": ["present if tier == high"], "default": "absent"},
        ),
    ]


def doc_examples() -> list[dict[str, Any]]:
    return [json.loads(line) for line in (FIXTURES / "doc_examples.jsonl").read_text().splitlines()]


def hand_sample() -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in (FIXTURES / "cc_hand_sample.jsonl").read_text().splitlines()
    ]


def gate_failures(rows: list[dict[str, Any]], tiers: list[str]) -> list[dict[str, Any]]:
    """Doc rows whose computed tier disagrees with the doc: an example "triggers at tier X" when
    the computed tier ranks at or above X."""
    bad = []
    for row, tier in zip(rows, tiers, strict=True):
        triggers = RANK[tier] >= RANK[row["want"]]
        if triggers != bool(row["triggers"]):
            bad.append({**row, "computed": tier})
    return bad
