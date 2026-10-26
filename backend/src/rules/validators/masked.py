"""``masked@1``: does a candidate read as a MASKED value of a format? (FR-016.P1; FTDD 016 15.1)

``valid`` here means "reads masked"; it bears no identifier (``value_bearing=False``), so a case
where it is true needs no public source. The rule (D32 starting values; the cases fix it):

1. **Mask characters:** ``X x * # • ● █`` (U+2022, U+25CF, U+2588). One value uses exactly ONE
   mask character; a mix reads ``mixed_mask_characters``.
2. **Characters:** only ASCII digits, the mask character and the format's delimiters (the IBAN
   also keeps upper-case letters), else ``illegal_character``.
3. **Length:** mask characters and kept characters together fill one of the format's lengths
   (an IBAN whose country code is kept: that country's length), else ``wrong_length``.
4. **Layout:** no delimiter, or one repeated delimiter at one of the format's popular group
   layouts (``schemes._digit_layout_popular``; the IBAN: groups of four), else
   ``irregular_layout``.
5. **Kept characters** form a leading run, a trailing run or both - never a kept character
   between two masked ones (``kept_characters_misplaced``) - each within the format's maximum
   (``too_many_kept``): card first 8 and last 4; IBAN country code and check digits, plus the last
   4; every other format the last 4. An IBAN's kept leading characters are letters then digits.
   An IBAN's BBAN may hold the letter ``X``: masked with another character, its ``X`` is a letter;
   masked with ``X``, its last four characters are kept from the first non-``X`` one onward.
   Kept digits are never checked for a check digit or a range.
6. **Boundary:** a neighbouring letter or digit reads ``alphanumeric_boundary``.

**Scan (its own, Q4 (a)):** a candidate starts at a mask character or digit (an IBAN: two
letters or masks, then two digits or masks) whose left neighbour is not a letter or digit, and runs
over those characters and the format's delimiters. So a word containing ``X`` is never a candidate.
The format validators' scans and ``checksum_validate@1`` are unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...operators.native.identifiers import schemes
from ...operators.native.identifiers.schemes import Verdict
from .base import Candidate, MaskShape, ValidatorDecl
from .formats import MASK_SHAPES

#: The signed-off mask characters (FTDD 016 section 15.1, Q1 (a)).
MASK_CHARACTERS = "Xx*#•●█"

_DIGITS = frozenset(schemes.DIGITS)
_UPPER = frozenset(schemes.UPPER)

MASKED_REASONS = (
    "masked",
    "no_characters",
    "illegal_character",
    "no_mask_character",
    "mixed_mask_characters",
    "wrong_length",
    "irregular_layout",
    "kept_characters_misplaced",
    "too_many_kept",
    "alphanumeric_boundary",
)


def _verdict(masked: bool, reason: str, normalised: str, layout: str, fmt: str) -> Verdict:
    return Verdict(masked, reason, "none", layout, normalised, fmt)


def _iban_trailing_letters_kept(kinds: str, positions: list[str], window: int) -> str:
    """An IBAN masked with ``X`` reads its last ``window`` characters as kept from the first
    character that is not ``X`` onward (the BBAN may end in the letter X)."""
    n = len(kinds)
    tail = positions[n - window :]
    first = next((i for i, c in enumerate(tail) if c != "X"), None)
    if first is None:
        return kinds
    return kinds[: n - window + first] + "k" * (window - first)


def _quads(n: int) -> tuple[int, ...]:
    return tuple([4] * (n // 4) + ([n % 4] if n % 4 else []))


class Masked:
    decl = ValidatorDecl(
        id="masked",
        version=1,
        description="Reads whether a candidate is a MASKED value of a format (valid = masked): "
        "one mask character (X x * # • ● █) with kept digits only at the start "
        "and end, filling the format's length in a popular layout.",
        params_schema={
            "type": "object",
            "properties": {"format": {"type": "string", "enum": sorted(MASK_SHAPES)}},
            "required": ["format"],
            "additionalProperties": False,
        },
        reasons=MASKED_REASONS,
        outputs=(
            ("valid", "bool"),
            ("reason", "string"),
            ("layout", "string"),
            ("normalised", "string"),
            ("detail", "string"),
        ),
        algorithms=("mask_shape",),
        value_bearing=False,
    )

    def check(self, value: str, before: str, after: str, params: Mapping[str, Any]) -> Verdict:
        fmt = str(params["format"])
        shape = MASK_SHAPES[fmt]
        iban = fmt == "iban"
        if not value:
            return _verdict(False, "no_characters", "", "none", fmt)
        allowed = (
            _DIGITS | set(MASK_CHARACTERS) | set(shape.delimiters) | (_UPPER if iban else set())
        )
        if any(c not in allowed for c in value):
            return _verdict(False, "illegal_character", "", "none", fmt)
        positions = [c for c in value if c not in shape.delimiters]
        normalised = "".join(positions)
        masks = {c for c in positions if c in MASK_CHARACTERS}
        if not masks:
            return _verdict(False, "no_mask_character", normalised, "none", fmt)
        if iban and len(masks) == 2 and "X" in masks:
            masks.discard("X")  # an IBAN masked with another character keeps its letter X
        if len(masks) > 1:
            return _verdict(False, "mixed_mask_characters", normalised, "none", fmt)
        mask = next(iter(masks))
        n = len(positions)
        if n not in self._lengths(shape, positions, mask, iban):
            return _verdict(False, "wrong_length", normalised, "none", fmt)
        core = "".join("0" if c not in shape.delimiters else c for c in value)
        layouts = (_quads(n),) if iban else shape.layouts
        if not schemes._digit_layout_popular(core, layouts, shape.delimiters):
            return _verdict(False, "irregular_layout", normalised, "irregular", fmt)
        kinds = "".join("m" if c == mask else "k" for c in positions)
        if iban and mask == "X":
            kinds = _iban_trailing_letters_kept(kinds, positions, shape.keep_trailing)
        lead = len(kinds) - len(kinds.lstrip("k"))
        trail = len(kinds) - len(kinds.rstrip("k"))
        if "k" in kinds[lead : n - trail] or lead == n:
            return _verdict(False, "kept_characters_misplaced", normalised, "popular", fmt)
        if lead > shape.keep_leading or trail > shape.keep_trailing:
            return _verdict(False, "too_many_kept", normalised, "popular", fmt)
        if iban and not all(
            (c in _UPPER) if i < 2 else (c in _DIGITS) for i, c in enumerate(positions[:lead])
        ):
            return _verdict(False, "kept_characters_misplaced", normalised, "popular", fmt)
        if not iban and any(c not in _DIGITS for c in positions if c != mask):
            return _verdict(False, "kept_characters_misplaced", normalised, "popular", fmt)
        if before[-1:].isalnum() or after[:1].isalnum():
            return _verdict(False, "alphanumeric_boundary", normalised, "popular", fmt)
        return _verdict(True, "masked", normalised, "popular", fmt)

    @staticmethod
    def _lengths(shape: MaskShape, positions: list[str], mask: str, iban: bool) -> tuple[int, ...]:
        if iban and len(positions) >= 2 and mask not in positions[:2]:
            country = "".join(positions[:2])
            if country in schemes.IBAN_REGISTRY:
                return (schemes.IBAN_REGISTRY[country][0],)
        return shape.lengths

    def scan(self, text: str, params: Mapping[str, Any]) -> list[Candidate]:
        fmt = str(params["format"])
        shape = MASK_SHAPES[fmt]
        iban = fmt == "iban"
        body = _DIGITS | set(MASK_CHARACTERS) | (_UPPER if iban else set())
        delims = set(shape.delimiters)
        out: list[Candidate] = []
        i, n = 0, len(text)
        while i < n:
            if not self._starts(text, i, body, iban):
                i += 1
                continue
            j = i
            while j < n and text[j] in body:
                j += 1
            while j < n and text[j] in delims:
                k = j
                while k < n and text[k] in delims:
                    k += 1
                if k < n and text[k] in body:
                    j = k
                    while j < n and text[j] in body:
                        j += 1
                else:
                    break
            out.append(Candidate(i, j, self.check(text[i:j], text[:i], text[j:], params)))
            i = j
        return out

    @staticmethod
    def _starts(text: str, i: int, body: set[str] | frozenset[str], iban: bool) -> bool:
        if text[i] not in body or (i > 0 and text[i - 1].isalnum()):
            return False
        if not iban:
            return True
        head = text[i : i + 4]
        masks = set(MASK_CHARACTERS)
        return (
            len(head) == 4
            and all(c in _UPPER or c in masks for c in head[:2])
            and all(c in _DIGITS or c in masks for c in head[2:])
        )


MASKED = Masked()
