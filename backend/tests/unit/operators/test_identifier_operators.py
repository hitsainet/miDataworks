"""The DLP identifier and text operators through 003's real executor (e2e DLP run gaps G0, G3, G8).

Oracles that do not agree with the code by construction:
- published check-digit vectors (ISO 13616's IBAN examples, the networks' test card numbers, an
  ABA number a Federal Reserve bank publishes, the NHS's own example number);
- the consolidated DLP doc's 62 documented examples with the tier and trigger the doc states;
- 321 rows of the e2e run's HAND-WRITTEN Credit Cards recipe output with the labels that recipe
  gave them (Jinja over 38 steps, independently re-verified on 32,000 rows by the run).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest

from src.operators.errors import StepFailed
from src.operators.native.identifiers import IDENTIFIER_OPERATORS, schemes
from src.operators.native.identifiers.text_ops import check_pattern, parse_rule
from src.operators.registry import OperatorRegistry
from tests.fixtures.humor_pool import with_system_columns
from tests.support import dlp
from tests.support.curation_fixtures import Ran, run_operator

NAMES = {
    "synth_identifier",
    "checksum_validate",
    "regex_match",
    "phrase_proximity",
    "rule_label",
    "drop_columns",
}


def _chain(table: pa.Table, roles: dict[str, str], steps: list[tuple[str, dict[str, Any]]]) -> Ran:
    ran: Ran | None = None
    for name, params in steps:
        ran = run_operator(name, params, table, roles)
        roles = dict(ran.meta["output_column_roles"])
        table = ran.output
    assert ran is not None
    return ran


def _index_rows(n: int) -> tuple[pa.Table, dict[str, str]]:
    return with_system_columns([{"idx": i} for i in range(n)], content=("idx",)), {"idx": "content"}


# --- the live registry ------------------------------------------------------------------------


def test_every_dlp_operator_is_in_the_registry_production_builds() -> None:
    production = OperatorRegistry.build(catalogues=(), entry_points=())
    states = {e.name: s for e, s in production.entries()}
    assert {cls.manifest.name for cls in IDENTIFIER_OPERATORS} == NAMES
    for name in NAMES:
        assert states.get(name) == "allowed", name


# --- check-digit vectors ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("scheme", "value", "valid", "reason"),
    [
        ("card_pan", "4111 1111 1111 1111", True, "ok"),  # Visa test number
        ("card_pan", "5555555555554444", True, "ok"),  # Mastercard test number
        ("card_pan", "378282246310005", True, "ok"),  # Amex test number
        ("card_pan", "4111 1111 1111 1112", False, "bad_check_digit"),
        ("card_pan", "7111 1111 1111 1114", False, "bad_prefix"),  # Luhn-valid, no issuer
        ("aba_routing", "011000015", True, "ok"),  # Federal Reserve Bank of Boston
        ("aba_routing", "011000016", False, "bad_check_digit"),
        ("aba_routing", "500000005", False, "bad_prefix"),  # 3-7-1 valid, prefix 50
        ("iban", "GB82 WEST 1234 5698 7654 32", True, "ok"),  # ISO 13616 example
        ("iban", "DE89370400440532013000", True, "ok"),
        ("iban", "GB82WEST12345698765433", False, "bad_check_digit"),
        ("iban", "GB82WEST1234569876543", False, "wrong_length"),
        ("ssn_us", "078-05-1120", True, "ok"),  # valid format (the famous wallet card number)
        ("ssn_us", "666-12-3456", False, "bad_range"),
        ("ssn_us", "123-00-4567", False, "bad_range"),
        ("nhs", "943 476 5919", True, "ok"),  # the NHS's published example
        ("nhs", "943 476 5918", False, "bad_check_digit"),
        ("nhs", "401 023 2137", True, "ok"),  # NHS test numbers (mutation C05 survived on one)
        ("nhs", "485 777 3457", True, "ok"),
        ("aadhaar", "2341 2341 2346", True, "ok"),
        ("aadhaar", "2341 2341 2345", False, "bad_check_digit"),
        ("aadhaar", "1341 2341 2349", False, "bad_check_digit"),
    ],
)
def test_published_vectors(scheme: str, value: str, valid: bool, reason: str) -> None:
    verdict = schemes.SCHEMES[scheme].check(value)
    assert (verdict.valid, verdict.reason) == (valid, reason), verdict


def test_the_aba_rule_is_3_7_1_not_luhn() -> None:
    """The doc says Luhn; 053902197 (a documented valid example) fails Luhn and passes 3-7-1."""
    assert not schemes.luhn_ok("053902197")
    assert schemes.SCHEMES["aba_routing"].check("053902197").tier == "medium"


# --- synth_identifier -------------------------------------------------------------------------


@pytest.mark.parametrize("scheme", list(schemes.SCHEME_NAMES))
def test_synth_identifier_valid_flag_is_the_validators_verdict(scheme: str, data_dir: Path) -> None:
    table, roles = _index_rows(400)
    ran = _chain(
        table,
        roles,
        [
            ("synth_identifier", {"scheme": scheme, "seed": 7, "valid_share": 0.4}),
            ("checksum_validate", {"column": "value", "scheme": scheme}),
        ],
    )
    rows = ran.output.to_pylist()
    assert {r["perturbation"] for r in rows} == {"none", *schemes.SCHEMES[scheme].perturbations}
    for r in rows:
        assert r["valid"] == r["cv_valid"], r
        assert r["scheme"] == scheme
        if r["perturbation"] == "none":
            # a valid value in a popular layout is medium; a delimited ABA number is low (doc)
            want = "medium" if r["cv_layout"] == "popular" else "low"
            assert (r["cv_valid"], r["cv_tier"]) == (True, want), r
        if r["perturbation"] in (
            "bad_check_digit",
            "wrong_length",
            "bad_prefix",
            "bad_range",
            "masked",
        ):
            assert r["cv_valid"] is False, r
    assert sum(r["perturbation"] == "none" for r in rows) == pytest.approx(160, abs=40)


def test_synth_identifier_is_a_function_of_seed_and_row(data_dir: Path) -> None:
    table, roles = _index_rows(50)
    params = {"scheme": "card_pan", "seed": 3}
    first = run_operator("synth_identifier", params, table, roles).output.column("value")
    again = run_operator("synth_identifier", params, table, roles).output.column("value")
    swapped = pa.concat_tables([table.slice(25), table.slice(0, 25)])
    reordered = run_operator("synth_identifier", params, swapped, roles)
    by_key = dict(
        zip(
            reordered.output.column("_dw_row_key").to_pylist(),
            reordered.output.column("value").to_pylist(),
            strict=True,
        )
    )
    other = run_operator("synth_identifier", {**params, "seed": 4}, table, roles).output.column(
        "value"
    )
    assert first.to_pylist() == again.to_pylist()
    assert [by_key[k] for k in table.column("_dw_row_key").to_pylist()] == first.to_pylist()
    assert first.to_pylist() != other.to_pylist()


def test_synth_identifier_refuses_what_the_scheme_cannot_make(data_dir: Path) -> None:
    table, roles = _index_rows(3)
    with pytest.raises(StepFailed, match="cannot make") as caught:
        run_operator(
            "synth_identifier",
            {"scheme": "ssn_us", "perturbations": ["bad_check_digit"]},
            table,
            roles,
        )
    assert caught.value.details["unsupported"] == ["bad_check_digit"]
    with pytest.raises(StepFailed, match="cannot be laid out"):
        run_operator(
            "synth_identifier", {"scheme": "ssn_us", "delimiters": ["slash"]}, table, roles
        )
    with pytest.raises(StepFailed, match="applies to the iban scheme only"):
        run_operator("synth_identifier", {"scheme": "card_pan", "countries": ["DE"]}, table, roles)


def test_card_test_bins_only_draw_from_the_published_test_bins(data_dir: Path) -> None:
    table, roles = _index_rows(60)
    ran = run_operator(
        "synth_identifier",
        {"scheme": "card_pan", "prefix_set": "test", "valid_share": 1.0, "delimiters": ["none"]},
        table,
        roles,
    )
    bins = {b for group in schemes.CARD_TEST_BINS.values() for b in group}
    assert all(v[:6] in bins for v in ran.output.column("value").to_pylist())


# --- the documented examples: the 62-row acceptance gate --------------------------------------


def _gate_tiers(dictionary: str, data_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    rows = [r for r in dlp.doc_examples() if r["dictionary"] == dictionary]
    table = with_system_columns(
        [
            {
                "p0": r["p0"],
                "example": r["example"],
                "p1": r["p1"],
                "text": r["p0"] + r["example"] + r["p1"],
                "i": i,
            }
            for i, r in enumerate(rows)
        ],
        content=("text",),
    )
    roles = {
        "text": "content",
        "p0": "metadata",
        "example": "metadata",
        "p1": "metadata",
        "i": "metadata",
    }
    ran = _chain(
        table,
        roles,
        dlp.label_steps(dictionary, value="example", before="p0", after="p1", text="text"),
    )
    out = sorted(ran.output.to_pylist(), key=lambda r: r["i"])
    return rows, [r["tier"] for r in out]


@pytest.mark.parametrize(
    ("dictionary", "count"), [("cc", 19), ("ssn", 18), ("aba", 18), ("iban", 7)]
)
def test_every_documented_example_passes(dictionary: str, count: int, data_dir: Path) -> None:
    rows, tiers = _gate_tiers(dictionary, data_dir)
    assert len(rows) == count
    assert dlp.gate_failures(rows, tiers) == []


def test_the_gate_has_62_rows() -> None:
    assert len(dlp.doc_examples()) == 62


# --- the hand-written recipe's labels, byte for byte ------------------------------------------


def test_the_four_step_chain_reproduces_the_hand_recipes_labels(data_dir: Path) -> None:
    sample = dlp.hand_sample()
    table = with_system_columns(
        [{k: r[k] for k in ("idx", "pre", "value", "post", "text")} for r in sample],
        content=("text",),
    )
    roles = {
        "text": "content",
        "idx": "metadata",
        "pre": "metadata",
        "value": "metadata",
        "post": "metadata",
    }
    ran = _chain(
        table, roles, dlp.label_steps("cc", value="value", before="pre", after="post", text="text")
    )
    got = {r["idx"]: (r["tier"], r["label"]) for r in ran.output.to_pylist()}
    want = {r["idx"]: (r["hand_tier"], r["hand_label"]) for r in sample}
    assert got == want
    labels = [got[r["idx"]][1] for r in sample]
    assert json.dumps(labels).encode() == json.dumps([r["hand_label"] for r in sample]).encode()
    assert {t for t, _ in got.values()} == {"none", "low", "medium", "high"}


# --- checksum_validate scan mode --------------------------------------------------------------


def test_scan_mode_finds_the_best_candidate_and_its_span(data_dir: Path) -> None:
    text = "Order 12345, card 4111-1111-1111-1111 exp 12/29"
    table = with_system_columns([{"text": text}])
    ran = run_operator(
        "checksum_validate",
        {"column": "text", "scheme": "card_pan", "mode": "scan"},
        table,
        {"text": "content"},
    )
    row = ran.output.to_pylist()[0]
    assert (row["cv_tier"], row["cv_valid"]) == ("medium", True)
    assert text[row["cv_start"] : row["cv_end"]] == "4111-1111-1111-1111"


def test_a_number_column_is_refused_not_stringified(data_dir: Path) -> None:
    table = with_system_columns([{"text": "a", "n": 9}])
    with pytest.raises(StepFailed, match="not text") as caught:
        run_operator(
            "checksum_validate", {"column": "n", "scheme": "ssn_us"}, table, {"text": "content"}
        )
    assert caught.value.code == "column_not_text"


# --- regex_match ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pattern", "rule"),
    [
        (r"(a)\1", "backreference"),
        (r"(?=a)a", "lookaround"),
        (r"(?<!x)a", "lookaround"),
        (r"(a)?(?(1)b|c)", "conditional"),
        (r"(?>ab)", "atomic"),
        (r"a++", "possessive"),
        (r"a{1001}", "repeat_too_large"),
        (r"(a+)+b", "nested_quantifier"),
        (r"(", "syntax"),
    ],
)
def test_regex_outside_the_subset_names_its_rule(pattern: str, rule: str) -> None:
    with pytest.raises(StepFailed) as caught:
        check_pattern(pattern)
    assert caught.value.details["rule"] == rule


def test_regex_match_records_every_span(data_dir: Path) -> None:
    table = with_system_columns(
        [{"text": "ssn 123-45-6789 and 987-65-4321"}, {"text": "none here"}]
    )
    ran = run_operator(
        "regex_match",
        {"column": "text", "pattern": r"\b\d{3}-\d{2}-\d{4}\b"},
        table,
        {"text": "content"},
        version="1",  # 016: @2 is the current version; this test is @1's
    )
    rows = {r["text"]: r for r in ran.output.to_pylist()}
    hit = rows["ssn 123-45-6789 and 987-65-4321"]
    assert (hit["rx_matched"], hit["rx_count"], hit["rx_first"]) == (True, 2, "123-45-6789")
    assert json.loads(hit["rx_spans"]) == [[4, 15], [20, 31]]
    assert (rows["none here"]["rx_matched"], rows["none here"]["rx_count"]) == (False, 0)


# --- phrase_proximity -------------------------------------------------------------------------


def test_proximity_is_measured_in_bytes_and_ignores_overlap(data_dir: Path) -> None:
    text = "card number:éé 4111 and Visa"
    start = text.index("4111")
    table = with_system_columns([{"text": text, "s": start, "e": start + 4}])
    ran = run_operator(
        "phrase_proximity",
        {
            "column": "text",
            "phrases": ["card number", "visa"],
            "max_distance_bytes": 4,
            "anchor_start_column": "s",
            "anchor_end_column": "e",
        },
        table,
        {"text": "content"},
    )
    row = ran.output.to_pylist()[0]
    # "card number" ends 4 characters but 6 UTF-8 bytes ("é" is two) before the anchor; "Visa"
    # starts 5 bytes after it, so in bytes Visa is the nearer one (in characters it would not be).
    assert (row["pp_distance"], row["pp_phrase"], row["pp_side"], row["pp_near"]) == (
        5,
        "Visa",
        "after",
        False,
    )


def test_per_key_phrases_apply_to_their_key_only(data_dir: Path) -> None:
    rows = [{"text": "Andorra AD14 x", "k": "AD"}, {"text": "Andorra AT48 x", "k": "AT"}]
    table = with_system_columns(rows)
    ran = run_operator(
        "phrase_proximity",
        {
            "column": "text",
            "phrases": ["IBAN"],
            "anchor_column": "k",
            "key_column": "k",
            "key_phrases": ["AD=Andorra", "AT=Austria"],
        },
        table,
        {"text": "content"},
    )
    near = {r["k"]: r["pp_near"] for r in ran.output.to_pylist()}
    assert near == {"AD": True, "AT": False}


# --- rule_label -------------------------------------------------------------------------------


def test_rule_parsing() -> None:
    assert parse_rule("high if t == medium and n == true") == (
        "high",
        [("t", "==", "medium"), ("n", "==", True)],
    )
    assert parse_rule("@x if a in [1, 'b'] and c is not null") == (
        "@x",
        [("a", "in", [1, "b"]), ("c", "is not null", None)],
    )
    with pytest.raises(StepFailed):
        parse_rule("high when t == medium")


def test_rule_label_compares_without_converting(data_dir: Path) -> None:
    table = with_system_columns([{"text": "a", "n": "9"}, {"text": "b", "n": "10"}])
    ran = run_operator(
        "rule_label", {"rules": ["nine if n == 9"], "default": "@n"}, table, {"text": "content"}
    )
    assert sorted(ran.output.column("label").to_pylist()) == ["10", "9"], "'9' is text, not 9"
    with pytest.raises(StepFailed, match="Cannot compare") as caught:
        run_operator("rule_label", {"rules": ["big if n > 5"]}, table, {"text": "content"})
    assert caught.value.code == "rule_type_mismatch"


# --- drop_columns -----------------------------------------------------------------------------


def test_drop_columns_removes_columns_and_their_roles(data_dir: Path) -> None:
    table = with_system_columns([{"text": "a", "vtmp_1": 1, "vtmp_2": 2, "keep": 3}])
    roles = {"text": "content", "vtmp_1": "metadata", "vtmp_2": "metadata", "keep": "metadata"}
    ran = run_operator("drop_columns", {"patterns": ["vtmp_*"]}, table, roles)
    assert "vtmp_1" not in ran.output.schema.names and "keep" in ran.output.schema.names
    assert set(ran.meta["output_column_roles"]) == {"text", "keep"}
    assert ran.events.num_rows == 0 and ran.output.num_rows == 1


@pytest.mark.parametrize(
    ("params", "code"),
    [
        ({"columns": ["missing"]}, "column_not_found"),
        ({"patterns": ["zz*"]}, "column_not_found"),
        ({"columns": ["text"]}, "column_protected"),
        ({"columns": ["_dw_split"]}, "column_protected"),
        ({}, "params_invalid"),
    ],
)
def test_drop_columns_refuses(params: dict[str, Any], code: str, data_dir: Path) -> None:
    table = with_system_columns([{"text": "a", "keep": 3}])
    with pytest.raises(StepFailed) as caught:
        run_operator("drop_columns", params, table, {"text": "content", "keep": "metadata"})
    assert caught.value.code == code


# --- mutation survivors of 2026-10-21 (C11, C13), now regression tests ------------------------


def test_the_neighbouring_characters_decide_the_boundary(data_dir: Path) -> None:
    """C11 survived: checksum_validate ignoring before_column/after_column passed every test,
    because no fixture row had a letter or digit touching its value. A letter before a card
    number makes it tier none (the doc's ``aa5269946762375011aa``); a space keeps it medium."""
    rows = [
        {"text": "x4111111111111111 y", "pre": "x", "value": "4111111111111111", "post": " y"},
        {"text": "a 4111111111111111 b", "pre": "a ", "value": "4111111111111111", "post": " b"},
    ]
    table = with_system_columns(rows)
    ran = run_operator(
        "checksum_validate",
        {"column": "value", "scheme": "card_pan", "before_column": "pre", "after_column": "post"},
        table,
        {"text": "content"},
    )
    tiers = {
        r["pre"]: (r["cv_tier"], r["cv_reason"], r["cv_start"]) for r in ran.output.to_pylist()
    }
    assert tiers == {"x": ("none", "alphanumeric_boundary", 1), "a ": ("medium", "ok", 2)}


def test_a_phrase_inside_the_anchor_is_not_near_it(data_dir: Path) -> None:
    """C13 survived: a phrase overlapping the anchor counted at distance 0."""
    text = "ref Visa4111 end"
    start, end = text.index("Visa"), text.index(" end")
    table = with_system_columns([{"text": text, "s": start, "e": end}])
    ran = run_operator(
        "phrase_proximity",
        {
            "column": "text",
            "phrases": ["visa"],
            "anchor_start_column": "s",
            "anchor_end_column": "e",
        },
        table,
        {"text": "content"},
    )
    row = ran.output.to_pylist()[0]
    assert (row["pp_distance"], row["pp_near"]) == (None, False)
