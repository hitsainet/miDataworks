"""The validator library (FR-016.1 - FR-016.3, FR-016.P1, FR-016.P2; FTASKS 1.1 - 1.7, 1.9).

Oracles that do not agree with the code by construction: the contract file's cases (expectations
typed by hand from the documented rules, external values cited), the private valid-path cases, and
the generator's own perturbations (``schemes.generate``), which the masked rule never sees.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from src.operators.native.identifiers import schemes
from src.rules import cases as rule_cases
from src.rules.validators import (
    VALIDATORS,
    Validator,
    ValidatorDecl,
    ValidatorUnknown,
    declaration,
    resolve,
    superseded_by,
)
from src.rules.validators.base import Candidate
from src.rules.validators.masked import MASK_CHARACTERS
from tests.support.source_tree import REPO

CASES = rule_cases.load_cases()
PRIVATE = REPO / "backend" / "tests" / "fixtures" / "private" / "rule-signal-private-cases.json"


def _sections() -> list[tuple[str, dict[str, Any]]]:
    out = [(ref, c) for ref, sec in CASES["validators"].items() for c in sec["cases"]]
    if PRIVATE.exists():
        private = json.loads(PRIVATE.read_text(encoding="utf-8"))
        out += [(ref, c) for ref, sec in private["validators"].items() for c in sec["cases"]]
    return out


ALL_CASES = _sections()


def _validator(ref: str) -> Validator:
    vid, version = ref.rsplit("@", 1)
    return resolve(vid, int(version))


# --- 1.1 the interface --------------------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(VALIDATORS))
def test_every_registered_entry_conforms_to_the_protocol(key: tuple[str, int]) -> None:
    validator = VALIDATORS[key]
    assert isinstance(validator, Validator)
    decl = validator.decl
    assert isinstance(decl, ValidatorDecl)
    assert (decl.id, decl.version) == key
    assert decl.reasons and len(set(decl.reasons)) == len(decl.reasons)
    assert {"valid", "reason"} <= {n for n, _ in decl.outputs}
    jsonschema.Draft202012Validator.check_schema(decl.params_schema)
    assert decl.algorithms


def test_only_aba_routing_is_an_institution_identifier() -> None:
    assert {k for k, v in VALIDATORS.items() if v.decl.institution_identifier} == {
        ("aba_routing", 1)
    }


def test_the_six_schemes_are_version_one_formats() -> None:
    assert {(name, 1) for name in schemes.SCHEMES} <= set(VALIDATORS)


# --- 1.2 declared reasons over every case of the live registry ----------------------------------


@pytest.mark.parametrize(
    ("ref", "case"), ALL_CASES, ids=lambda x: x if isinstance(x, str) else x["id"]
)
def test_every_case_passes_and_its_reasons_are_declared(ref: str, case: dict[str, Any]) -> None:
    validator = _validator(ref)
    outcome = rule_cases.run_validator_case(validator, case)
    assert outcome.passed, outcome.mismatches
    reasons = (
        [c["reason"] for c in outcome.actual["candidates"]]
        if "candidates" in outcome.actual
        else [outcome.actual["reason"]]
    )
    undeclared = set(reasons) - set(validator.decl.reasons)
    assert not undeclared, f"{ref} returned undeclared reasons {undeclared}"


def test_generated_values_return_only_declared_reasons() -> None:
    """Thousands of generator outputs (every perturbation) per format: every reason is declared."""
    for name, scheme in schemes.SCHEMES.items():
        validator = resolve(name, 1)
        for seed in range(300):
            rng = random.Random(seed)
            perturbation = rng.choice(("none", *scheme.perturbations))
            made = schemes.generate(
                name, rng, perturbation=perturbation, style=rng.choice(scheme.styles)
            )
            for before, after in (("", ""), ("x", ""), ("", "7")):
                reason = validator.check(made.value, before, after, {}).reason
                assert reason in validator.decl.reasons, (name, made.value, reason)


# --- 1.3 the all-candidates scan ------------------------------------------------------------------


def test_scan_returns_every_candidate_in_text_order_with_code_point_spans() -> None:
    text = "éé 4111 1111 1111 1111 x 5555555555554444"
    found = resolve("card_pan", 1).scan(text, {})
    assert [(c.start, c.end) for c in found] == [(3, 22), (25, 41)]
    assert [text[c.start : c.end] for c in found] == ["4111 1111 1111 1111", "5555555555554444"]
    assert all(isinstance(c, Candidate) and c.verdict.valid for c in found)


def test_scan_candidates_are_checked_with_their_neighbours() -> None:
    found = resolve("card_pan", 1).scan("x4111111111111111", {})
    assert [c.verdict.reason for c in found] == ["alphanumeric_boundary"]


# --- 1.5 algorithm validators ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ref", "params"),
    [
        ("mod97_10@1", {}),
        ("mod97_10@1", {"rearrange": "both"}),
        ("weighted_mod@1", {"weights": [3, 7], "modulus": 10}),
        ("weighted_mod@1", {"weights": [3], "modulus": 10, "check_position": "last"}),
        ("weighted_mod@1", {"weights": [3, 7], "modulus": 1, "check_position": "last"}),
        ("length_charset@1", {"lengths": [], "charset": "digits"}),
        ("length_charset@1", {"lengths": [4], "charset": "hex"}),
        ("luhn@1", {"extra": True}),
    ],
)
def test_params_schemas_refuse_bad_params(ref: str, params: dict[str, Any]) -> None:
    schema = _validator(ref).decl.params_schema
    assert list(jsonschema.Draft202012Validator(schema).iter_errors(params))


def test_weighted_mod_expresses_aba_and_nhs_rules() -> None:
    aba = {"weights": [3, 7, 1] * 3, "modulus": 10, "check_position": "last"}
    nhs = {"weights": list(range(10, 0, -1)), "modulus": 11, "check_position": "last"}
    wm = resolve("weighted_mod", 1)
    for seed in range(200):
        rng = random.Random(seed)
        a, _ = schemes.SCHEMES["aba_routing"].make_valid(rng)
        n, _ = schemes.SCHEMES["nhs"].make_valid(rng)
        assert wm.check(a, "", "", aba).valid and wm.check(n, "", "", nhs).valid
        assert not wm.check(a[:-1] + str((int(a[-1]) + 1) % 10), "", "", aba).valid


# --- 1.6 masked@1 (FR-016.P1) ---------------------------------------------------------------------


@pytest.mark.parametrize("fmt", sorted(schemes.SCHEMES))
def test_every_mask_output_reads_masked(fmt: str) -> None:
    masked = resolve("masked", 1)
    scheme = schemes.SCHEMES[fmt]
    for seed in range(1000):
        rng = random.Random(seed)
        made = schemes.generate(fmt, rng, perturbation="masked", style=rng.choice(scheme.styles))
        verdict = masked.check(made.value, "", "", {"format": fmt})
        assert verdict.valid and verdict.reason == "masked", (made.value, verdict)
        found = masked.scan(made.value, {"format": fmt})
        assert [(c.start, c.end, c.verdict.valid) for c in found] == [(0, len(made.value), True)]


@pytest.mark.parametrize("fmt", sorted(schemes.SCHEMES))
def test_glued_letters_never_read_masked(fmt: str) -> None:
    masked = resolve("masked", 1)
    scheme = schemes.SCHEMES[fmt]
    for seed in range(1000):
        rng = random.Random(seed)
        made = schemes.generate(
            fmt, rng, perturbation="adjacent_letters", style=rng.choice(scheme.styles)
        )
        value = made.value
        assert not masked.check(value, "", "", {"format": fmt}).valid, value
        assert not any(c.verdict.valid for c in masked.scan(value, {"format": fmt})), value


@pytest.mark.parametrize("word", ["XEROX", "eXXXon", "Xmas", "box", "XXX", "xxxx", "XX-XX"])
def test_a_word_containing_x_reads_not_masked(word: str) -> None:
    masked = resolve("masked", 1)
    for fmt in schemes.SCHEMES:
        assert not masked.check(word, "", "", {"format": fmt}).valid
        assert not any(c.verdict.valid for c in masked.scan(f"the {word} here", {"format": fmt}))


def test_every_signed_off_mask_character_masks() -> None:
    masked = resolve("masked", 1)
    assert MASK_CHARACTERS == "Xx*#•●█"
    for ch in MASK_CHARACTERS:
        assert masked.check(f"{ch * 3}-{ch * 2}-1234", "", "", {"format": "ssn_us"}).valid, ch


# --- 1.7 dispatch by exact pin ----------------------------------------------------------------------


class _LuhnV2:
    """A fixture version 2 whose verdict differs from version 1 on every value."""

    decl = ValidatorDecl(
        id="luhn",
        version=2,
        description="fixture",
        params_schema={"type": "object"},
        reasons=("v2",),
        outputs=(("valid", "bool"), ("reason", "string")),
        algorithms=("fixture",),
    )

    def check(self, value: str, before: str, after: str, params: Any) -> Any:
        return schemes.Verdict(False, "v2", "none", "none", value, None)

    def scan(self, text: str, params: Any) -> list[Candidate]:
        return []


def test_a_rule_pinned_to_version_one_runs_version_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(VALIDATORS, ("luhn", 2), _LuhnV2())
    assert resolve("luhn", 1).check("4111111111111111", "", "", {}).reason == "ok"
    assert resolve("luhn", 2).check("4111111111111111", "", "", {}).reason == "v2"
    assert superseded_by("luhn", 1) == 2
    assert declaration(resolve("luhn", 1))["superseded_by"] == 2
    assert superseded_by("luhn", 2) is None
    with pytest.raises(ValidatorUnknown):
        resolve("luhn", 3)


def test_only_iban_at_one_is_superseded_and_by_two() -> None:
    """D38-1: iban@2 supersedes iban@1 (derived from the registry); nothing else is superseded."""
    assert {(i, v): superseded_by(i, v) for i, v in VALIDATORS if superseded_by(i, v)} == {
        ("iban", 1): 2
    }


def test_the_private_cases_ship_only_privately() -> None:
    """Not vacuous on a full checkout; on the mirror the directory is stripped (3.9)."""
    if not PRIVATE.exists():
        pytest.skip(
            "CROSS-REPO/MIRROR: backend/tests/fixtures/private/ is stripped from the mirror"
        )
    assert Path(PRIVATE).read_text(encoding="utf-8").startswith("{")
