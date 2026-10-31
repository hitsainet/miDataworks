"""Version 1 of the six format validators (FR-016.1, FR-016.3; FTID 016 section 3.1).

Each wraps a scheme of ``schemes.SCHEMES`` UNCHANGED: ``check`` is ``Scheme.check``. The declared
``reasons`` are the literals each ``Scheme.check`` can return, read from the code
(``schemes.py``: ``_none("...")``, the ``reason`` variables and the ``Verdict(...)`` calls);
``tests/unit/rules/test_validators.py`` pins them over every case of the contract file, and adding an
undeclared literal to a ``check`` turns it red (mutation M29).

``scan`` finds candidates with the patterns ``checksum_validate@1`` has always used (formerly
``identifier_ops._candidates``; Python ``re`` with ``[0-9]``, ASCII by construction) and checks each
one with the text on either side, exactly as ``check_one`` did. Spans are code points.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from ...operators.native.identifiers import schemes
from ...operators.native.identifiers.schemes import Verdict
from .base import Candidate, MaskShape, ValidatorDecl

#: The verdict's outputs, in the order ``checksum_validate@1`` writes them.
FORMAT_OUTPUTS: tuple[tuple[str, str], ...] = (
    ("valid", "bool"),
    ("reason", "string"),
    ("tier", "string"),
    ("layout", "string"),
    ("normalised", "string"),
    ("detail", "string"),
)

#: Every reason each ``Scheme.check`` can return, read from ``schemes.py``.
FORMAT_REASONS: dict[str, tuple[str, ...]] = {
    "card_pan": (
        "ok",
        "no_digits",
        "illegal_character",
        "wrong_length",
        "bad_check_digit",
        "bad_prefix",
        "alphanumeric_boundary",
        "irregular_layout",
    ),
    "ssn_us": (
        "ok",
        "no_digits",
        "illegal_character",
        "wrong_length",
        "bad_range",
        "irregular_layout",
        "letter_boundary",
    ),
    "aba_routing": (
        "ok",
        "no_digits",
        "illegal_character",
        "wrong_length",
        "bad_check_digit",
        "bad_prefix",
        "irregular_layout",
        "letter_boundary",
    ),
    "iban": (
        "ok",
        "no_characters",
        "not_uppercase",
        "illegal_character",
        "bad_prefix",
        "wrong_length",
        "bad_check_digit",
        "bad_bban_format",
        "alphanumeric_boundary",
    ),
    "nhs": (
        "ok",
        "no_digits",
        "illegal_character",
        "wrong_length",
        "bad_check_digit",
        "alphanumeric_boundary",
        "irregular_layout",
    ),
    "aadhaar": (
        "ok",
        "no_digits",
        "illegal_character",
        "wrong_length",
        "bad_check_digit",
        "bad_prefix",
        "alphanumeric_boundary",
        "irregular_layout",
    ),
}

FORMAT_ALGORITHMS: dict[str, tuple[str, ...]] = {
    "card_pan": ("luhn", "issuer_range_and_length"),
    "ssn_us": ("ssa_area_group_serial_ranges",),
    "aba_routing": ("aba_3_7_1", "federal_reserve_prefix"),
    "iban": ("mod97_10", "iban_country_length", "bban_format"),
    "nhs": ("nhs_mod_11",),
    "aadhaar": ("verhoeff", "aadhaar_leading_digit"),
}

_IBAN_LENGTHS = tuple(sorted({length for length, _, _ in schemes.IBAN_REGISTRY.values()}))

#: The masked shapes (FTDD 016 section 15.1; D32 starting values, signed off by cases).
MASK_SHAPES: dict[str, MaskShape] = {
    "card_pan": MaskShape(
        lengths=tuple(range(12, 20)),
        layouts=((4, 4, 4, 4), (4, 6, 5), (4, 6, 4)),
        delimiters=schemes.SCHEMES["card_pan"].delimiters,
        keep_leading=8,
        keep_trailing=4,
    ),
    "ssn_us": MaskShape((9,), ((3, 2, 4),), schemes.SCHEMES["ssn_us"].delimiters, 0, 4),
    "aba_routing": MaskShape((9,), ((3, 4, 2),), schemes.SCHEMES["aba_routing"].delimiters, 0, 4),
    "iban": MaskShape(_IBAN_LENGTHS, (), schemes.SCHEMES["iban"].delimiters, 4, 4),
    "nhs": MaskShape((10,), ((3, 3, 4),), schemes.SCHEMES["nhs"].delimiters, 0, 4),
    "aadhaar": MaskShape((12,), ((4, 4, 4),), schemes.SCHEMES["aadhaar"].delimiters, 0, 4),
}

_IBAN_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "countries": {
            "type": "array",
            "items": {"type": "string", "enum": sorted(schemes.IBAN_REGISTRY)},
            "uniqueItems": True,
            "description": "The listed countries (default: every country in the registry).",
        },
        "check_bban": {
            "type": "boolean",
            "description": "The country's BBAN letter/digit layout must match too (default true).",
        },
    },
    "additionalProperties": False,
}
_NO_PARAMS: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}


def candidate_spans(scheme: schemes.Scheme, text: str) -> list[tuple[int, int]]:
    """The candidate spans ``checksum_validate@1`` has always scanned (code points)."""
    if scheme.name == "iban":
        pattern = r"[A-Za-z]{2}[0-9]{2}(?: ?[A-Za-z0-9])+"
    else:
        delims = re.escape(scheme.delimiters)
        pattern = rf"[0-9](?:[{delims}]*[0-9])*"
    return [(m.start(), m.end()) for m in re.finditer(pattern, text)]


def scheme_opts(params: Mapping[str, Any]) -> dict[str, Any]:
    """The check options a rule's ``params`` carry (iban only: ``countries``, ``check_bban``)."""
    opts: dict[str, Any] = {}
    if params.get("countries"):
        opts["countries"] = list(params["countries"])
    if params.get("check_bban") is not None:
        opts["check_bban"] = bool(params["check_bban"])
    return opts


class FormatValidator:
    """Version 1 of one format: ``Scheme.check`` unchanged, plus the all-candidates scan."""

    def __init__(self, scheme: schemes.Scheme) -> None:
        self.scheme = scheme
        self.decl = ValidatorDecl(
            id=scheme.name,
            version=1,
            description=scheme.description,
            params_schema=_IBAN_PARAMS if scheme.name == "iban" else _NO_PARAMS,
            reasons=FORMAT_REASONS[scheme.name],
            outputs=FORMAT_OUTPUTS,
            algorithms=FORMAT_ALGORITHMS[scheme.name],
            institution_identifier=scheme.name == "aba_routing",
            mask_shape=MASK_SHAPES[scheme.name],
        )

    def check(self, value: str, before: str, after: str, params: Mapping[str, Any]) -> Verdict:
        return self.scheme.check(value, before, after, **scheme_opts(params))

    def scan(
        self, text: str, params: Mapping[str, Any], *, before: str = "", after: str = ""
    ) -> list[Candidate]:
        """Every candidate in ``text``. ``before``/``after`` extend the neighbouring text beyond
        ``text`` (``checksum_validate@1``'s value columns); a rule scans with neither."""
        opts = scheme_opts(params)
        return [
            Candidate(
                start,
                end,
                self.scheme.check(
                    text[start:end], before + text[:start], text[end:] + after, **opts
                ),
            )
            for start, end in candidate_spans(self.scheme, text)
        ]


_HEAD = re.compile(r"[A-Za-z]{2}[0-9]{2}")
_ALNUM = re.compile(r"[A-Za-z0-9]+")


def _continues_bban(group: str, at: int, kinds: str) -> bool:
    """Whether ``group`` can sit at BBAN position ``at`` of a country whose BBAN kinds are
    ``kinds`` (``n`` digit, ``a`` letter, ``c`` letter or digit; letters in either case, so a
    lower-case IBAN is still one candidate and ``check`` names it ``not_uppercase``)."""
    if at + len(group) > len(kinds):
        return False
    for ch, kind in zip(group, kinds[at:], strict=False):
        if kind == "n" and not ch.isdigit():
            return False
        if kind == "a" and not ch.isalpha():
            return False
    return True


def iban_v2_spans(text: str) -> list[tuple[int, int]]:
    """``iban@2``'s candidates (D38-1): the head ``[A-Za-z]{2}[0-9]{2}`` and the alphanumeric run
    it starts, then each following group (one space, then letters and digits) only while that group
    keeps the compact length within the country's registered IBAN length and can continue the
    country's BBAN. An unlisted country has no BBAN to continue, so its candidate is the head's run
    alone (Review R1 of slices 2-4: "within the longest registered length" let an invalid head such
    as ``NV20`` in ``INV2024`` swallow the real IBAN that followed it). So a spaced IBAN followed by
    a word ends at its last group, where ``iban@1`` ran on through the word."""
    spans: list[tuple[int, int]] = []
    pos = 0
    while (head := _HEAD.search(text, pos)) is not None:
        start = head.start()
        run = _ALNUM.match(text, start)
        assert run is not None
        end = run.end()
        compact = len(text[start:end])
        entry = schemes.IBAN_REGISTRY.get(text[start : start + 2].upper())
        if entry is None:  # an unlisted country continues no group
            spans.append((start, end))
            pos = end
            continue
        limit = entry[0]
        kinds = schemes.bban_pattern(entry[1])
        while end + 1 < len(text) and text[end] == " ":
            group = _ALNUM.match(text, end + 1)
            if group is None:
                break
            chars = group.group()
            if compact + len(chars) > limit:
                break
            if not _continues_bban(chars, compact - 4, kinds):
                break
            compact += len(chars)
            end = group.end()
        spans.append((start, end))
        pos = end
    return spans


class IbanV2(FormatValidator):
    """``iban@2`` (D38-1, operator 2026-10-23): ``iban@1``'s check UNCHANGED, with a scan that
    stops at the group that cannot belong to the IBAN (:func:`iban_v2_spans`). ``iban@1`` keeps
    its published behaviour, and ``checksum_validate@1``'s scan stays ``iban@1``'s."""

    def __init__(self) -> None:
        super().__init__(schemes.SCHEMES["iban"])
        self.decl = ValidatorDecl(
            id="iban",
            version=2,
            description=self.scheme.description
            + "; the scan stops at a group past the country's length or BBAN",
            params_schema=_IBAN_PARAMS,
            reasons=FORMAT_REASONS["iban"],
            outputs=FORMAT_OUTPUTS,
            algorithms=FORMAT_ALGORITHMS["iban"],
            institution_identifier=False,
            mask_shape=MASK_SHAPES["iban"],
        )

    def scan(
        self, text: str, params: Mapping[str, Any], *, before: str = "", after: str = ""
    ) -> list[Candidate]:
        opts = scheme_opts(params)
        return [
            Candidate(
                start,
                end,
                self.scheme.check(
                    text[start:end], before + text[:start], text[end:] + after, **opts
                ),
            )
            for start, end in iban_v2_spans(text)
        ]


FORMAT_VALIDATORS: tuple[FormatValidator, ...] = (
    *(FormatValidator(scheme) for scheme in schemes.SCHEMES.values()),
    IbanV2(),
)
