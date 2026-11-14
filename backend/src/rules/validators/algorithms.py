"""Version 1 of the generic algorithm validators (FTDD 016 section 5.4, Q21 (a)).

``luhn``, ``mod97_10``, ``verhoeff``, ``weighted_mod`` and ``length_charset`` check the normalised
value of a candidate - typically the ``span_of`` a pattern signal, so a rule set can validate a
format the library does not know. They reuse the check-digit code of ``schemes.py``.

**Normalisation (stated in the contract):** ASCII spaces and hyphens are removed; nothing else is
changed. What remains must be ASCII digits (``mod97_10`` and ``length_charset`` with
``upper_alnum``: ASCII digits and upper-case letters), else the reason is ``illegal_character``.

**Scan:** candidates are maximal runs of the validator's characters joined by single spaces or
hyphens (``[0-9](?:[ -]?[0-9])*``, or with ``A-Z`` added), in text order, spans in code points.

The verdict's ``tier`` and ``layout`` are always ``none`` (these validators state validity only).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from ...operators.native.identifiers import schemes
from ...operators.native.identifiers.schemes import Verdict
from .base import Candidate, ValidatorDecl

ALGORITHM_OUTPUTS: tuple[tuple[str, str], ...] = (
    ("valid", "bool"),
    ("reason", "string"),
    ("normalised", "string"),
)

_DIGIT_RUN = re.compile(r"[0-9](?:[ -]?[0-9])*")
_ALNUM_RUN = re.compile(r"[0-9A-Z](?:[ -]?[0-9A-Z])*")
_UPPER_ALNUM = frozenset(schemes.ALNUM_UPPER)
_DIGITS = frozenset(schemes.DIGITS)


def _normalise(value: str) -> str:
    return value.replace(" ", "").replace("-", "")


def _verdict(valid: bool, reason: str, normalised: str) -> Verdict:
    return Verdict(valid, reason, "none", "none", normalised, None)


class _Algorithm:
    decl: ValidatorDecl
    _run: re.Pattern[str] = _DIGIT_RUN

    def check(self, value: str, before: str, after: str, params: Mapping[str, Any]) -> Verdict:
        raise NotImplementedError

    def _candidate_pattern(self, params: Mapping[str, Any]) -> re.Pattern[str]:
        return self._run

    def scan(self, text: str, params: Mapping[str, Any]) -> list[Candidate]:
        return [
            Candidate(
                m.start(),
                m.end(),
                self.check(m.group(), text[: m.start()], text[m.end() :], params),
            )
            for m in self._candidate_pattern(params).finditer(text)
        ]


def _digits_or_refusal(value: str, minimum: int) -> tuple[str, Verdict | None]:
    d = _normalise(value)
    if not d:
        return d, _verdict(False, "no_digits", d)
    if any(c not in _DIGITS for c in d):
        return d, _verdict(False, "illegal_character", d)
    if len(d) < minimum:
        return d, _verdict(False, "too_short", d)
    return d, None


class Luhn(_Algorithm):
    decl = ValidatorDecl(
        id="luhn",
        version=1,
        description="Luhn (ISO/IEC 7812-1 Annex B) check digit over ASCII digits; spaces and "
        "hyphens are ignored; at least two digits.",
        params_schema={"type": "object", "properties": {}, "additionalProperties": False},
        reasons=("ok", "no_digits", "illegal_character", "too_short", "bad_check_digit"),
        outputs=ALGORITHM_OUTPUTS,
        algorithms=("luhn",),
    )

    def check(self, value: str, before: str, after: str, params: Mapping[str, Any]) -> Verdict:
        d, refused = _digits_or_refusal(value, 2)
        if refused is not None:
            return refused
        if not schemes.luhn_ok(d):
            return _verdict(False, "bad_check_digit", d)
        return _verdict(True, "ok", d)


class Verhoeff(_Algorithm):
    decl = ValidatorDecl(
        id="verhoeff",
        version=1,
        description="Verhoeff check digit (dihedral group D5) over ASCII digits; spaces and "
        "hyphens are ignored; at least two digits.",
        params_schema={"type": "object", "properties": {}, "additionalProperties": False},
        reasons=("ok", "no_digits", "illegal_character", "too_short", "bad_check_digit"),
        outputs=ALGORITHM_OUTPUTS,
        algorithms=("verhoeff",),
    )

    def check(self, value: str, before: str, after: str, params: Mapping[str, Any]) -> Verdict:
        d, refused = _digits_or_refusal(value, 2)
        if refused is not None:
            return refused
        if not schemes.verhoeff_ok(d):
            return _verdict(False, "bad_check_digit", d)
        return _verdict(True, "ok", d)


class Mod97_10(_Algorithm):
    decl = ValidatorDecl(
        id="mod97_10",
        version=1,
        description="ISO 7064 MOD 97-10 over ASCII digits and upper-case letters (A=10 ... Z=35); "
        "valid when the number mod 97 is 1. rearrange=iban first moves the first four characters "
        "to the end (ISO 13616); rearrange=none checks the value as written.",
        params_schema={
            "type": "object",
            "properties": {"rearrange": {"type": "string", "enum": ["none", "iban"]}},
            "required": ["rearrange"],
            "additionalProperties": False,
        },
        reasons=("ok", "no_characters", "illegal_character", "too_short", "bad_check_digit"),
        outputs=ALGORITHM_OUTPUTS,
        algorithms=("mod97_10",),
    )
    _run = _ALNUM_RUN

    def check(self, value: str, before: str, after: str, params: Mapping[str, Any]) -> Verdict:
        t = _normalise(value)
        if not t:
            return _verdict(False, "no_characters", t)
        if any(c not in _UPPER_ALNUM for c in t):
            return _verdict(False, "illegal_character", t)
        iban = params.get("rearrange") == "iban"
        if len(t) < (5 if iban else 2):
            return _verdict(False, "too_short", t)
        arranged = t[4:] + t[:4] if iban else t
        if int(schemes.iban_numeric(arranged)) % 97 != 1:
            return _verdict(False, "bad_check_digit", t)
        return _verdict(True, "ok", t)


class WeightedMod(_Algorithm):
    decl = ValidatorDecl(
        id="weighted_mod",
        version=1,
        description="Weighted sum check: the value has exactly one digit per weight and "
        "sum(digit_i * weight_i) mod modulus is 0, the check digit being the last digit "
        "(check_position=last). ABA routing numbers are weights 3,7,1,3,7,1,3,7,1 modulus 10; "
        "NHS numbers are weights 10,9,...,2,1 modulus 11.",
        params_schema={
            "type": "object",
            "properties": {
                "weights": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 0, "maximum": 99},
                    "minItems": 2,
                    "maxItems": 64,
                },
                "modulus": {"type": "integer", "minimum": 2, "maximum": 97},
                "check_position": {"type": "string", "enum": ["last"]},
            },
            "required": ["weights", "modulus", "check_position"],
            "additionalProperties": False,
        },
        reasons=("ok", "no_digits", "illegal_character", "wrong_length", "bad_check_digit"),
        outputs=ALGORITHM_OUTPUTS,
        algorithms=("weighted_mod",),
    )

    def check(self, value: str, before: str, after: str, params: Mapping[str, Any]) -> Verdict:
        d, refused = _digits_or_refusal(value, 1)
        if refused is not None:
            return refused
        weights = [int(w) for w in params["weights"]]
        if len(d) != len(weights):
            return _verdict(False, "wrong_length", d)
        total = sum((ord(c) - 48) * w for c, w in zip(d, weights, strict=True))
        if total % int(params["modulus"]) != 0:
            return _verdict(False, "bad_check_digit", d)
        return _verdict(True, "ok", d)


class LengthCharset(_Algorithm):
    decl = ValidatorDecl(
        id="length_charset",
        version=1,
        description="Length and character set only (no check digit): the normalised value's "
        "length is one of lengths and every character is in the charset (digits: 0-9; "
        "upper_alnum: 0-9 and A-Z).",
        params_schema={
            "type": "object",
            "properties": {
                "lengths": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 1, "maximum": 64},
                    "minItems": 1,
                    "maxItems": 16,
                    "uniqueItems": True,
                },
                "charset": {"type": "string", "enum": ["digits", "upper_alnum"]},
            },
            "required": ["lengths", "charset"],
            "additionalProperties": False,
        },
        reasons=("ok", "no_characters", "illegal_character", "wrong_length"),
        outputs=ALGORITHM_OUTPUTS,
        algorithms=("length_charset",),
    )

    def _candidate_pattern(self, params: Mapping[str, Any]) -> re.Pattern[str]:
        return _ALNUM_RUN if params.get("charset") == "upper_alnum" else _DIGIT_RUN

    def check(self, value: str, before: str, after: str, params: Mapping[str, Any]) -> Verdict:
        t = _normalise(value)
        if not t:
            return _verdict(False, "no_characters", t)
        allowed = _UPPER_ALNUM if params["charset"] == "upper_alnum" else _DIGITS
        if any(c not in allowed for c in t):
            return _verdict(False, "illegal_character", t)
        if len(t) not in {int(n) for n in params["lengths"]}:
            return _verdict(False, "wrong_length", t)
        return _verdict(True, "ok", t)


ALGORITHM_VALIDATORS: tuple[_Algorithm, ...] = (
    Luhn(),
    Mod97_10(),
    Verhoeff(),
    WeightedMod(),
    LengthCharset(),
)
