"""Identifier schemes: check-digit algorithms, validity rules, confidence tiers and generators.

Pure standard-library code, shared by ``synth_identifier`` (which makes values) and
``checksum_validate`` (which checks them), so the two can never disagree about what "valid" means:
the generator's ``valid`` flag IS this module's verdict on the value it produced.

What each scheme checks (the 2026-10-21 end-to-end run's reading of the documented rules, 2026-10-21, verified
against its 62 documented examples and 32,000 rows):

- ``card_pan`` (ISO/IEC 7812 PAN): 12 to 19 digits, Luhn, and an issuer range + length (Visa 4,
  13/16/19; Mastercard 51-55 and 2221-2720, 16; Amex 34/37, 15; Diners 36/38/300-305, 14;
  Discover 6011/644-649/65, 16-19; JCB 3528-3589, 16-19). Delimiters ``- . space / :``.
- ``ssn_us`` (Social Security Administration rules): 9 digits; area not 000, 666 or 900-999; group
  not 00; serial not 0000. There is NO check digit and no documented "test" range of VALID numbers:
  since 2011 any valid-format number may be issued, so a generated positive can coincide with a
  real one by chance (nothing links it to a person). Negatives use the never-issued ranges above.
  Delimiters ``- . space``.
- ``aba_routing`` (ABA routing transit number): 9 digits, 3-7-1 weighted sum mod 10, prefix 00-12,
  21-32, 61-72 or 80. Not Luhn: the consolidated identifier documentation says Luhn, and 4 of its own 9 valid
  examples fail Luhn while all pass 3-7-1.
- ``iban`` (ISO 13616): country code in the configured list, the country's length, two check
  digits, MOD 97-10 = 1, and (``check_bban``) the country's BBAN format. Spaces only.
- ``nhs`` (NHS number, Mod 11) and ``aadhaar`` (Verhoeff): the extension points, implemented.
  Adding a scheme is one :class:`Scheme` subclass and one line in :data:`SCHEMES`.

Tiers (none / low / medium) follow the documented confidence tables; ``high`` needs a phrase
near the value, which ``phrase_proximity`` measures (a separate step).
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Sequence
from dataclasses import dataclass

DIGITS = "0123456789"
UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
ALNUM_UPPER = UPPER + DIGITS

TIERS = ("none", "low", "medium")
TIER_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}

#: Delimiter styles a generator can lay a value out with, and the character each one uses.
STYLES: dict[str, str] = {
    "none": "",
    "space": " ",
    "dash": "-",
    "dot": ".",
    "slash": "/",
    "colon": ":",
}

#: Every perturbation a generator knows, in a fixed order.
PERTURBATIONS = (
    "bad_check_digit",
    "wrong_length",
    "mixed_delimiters",
    "adjacent_letters",
    "bad_prefix",
    "bad_range",
    "masked",
)


# --------------------------------------------------------------------------------------------
# Check-digit algorithms
# --------------------------------------------------------------------------------------------


def luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        x = ord(ch) - 48
        if i % 2:
            x *= 2
            if x > 9:
                x -= 9
        total += x
    return total % 10 == 0


def luhn_check_digit(body: str) -> str:
    """The digit that makes ``body + digit`` Luhn-valid."""
    for d in DIGITS:
        if luhn_ok(body + d):
            return d
    raise AssertionError("unreachable: one of ten digits always completes Luhn")


def aba_ok(digits: str) -> bool:
    if len(digits) != 9:
        return False
    weights = (3, 7, 1) * 3
    return sum((ord(c) - 48) * w for c, w in zip(digits, weights, strict=True)) % 10 == 0


def aba_check_digit(body8: str) -> str:
    for d in DIGITS:
        if aba_ok(body8 + d):
            return d
    raise AssertionError("unreachable")


def iban_numeric(text: str) -> str:
    return "".join(c if c in DIGITS else str(ord(c) - 55) for c in text)


def mod97(iban: str) -> int:
    """ISO 7064 MOD 97-10 of an IBAN (country, check digits, BBAN), as the standard rearranges it."""
    return int(iban_numeric(iban[4:] + iban[:4])) % 97


def iban_check_digits(country: str, bban: str) -> str:
    return f"{98 - int(iban_numeric(bban + country + '00')) % 97:02d}"


def nhs_check_digit(body9: str) -> str | None:
    """Mod 11 with weights 10..2; 11 -> 0; 10 means no valid number has this body (None)."""
    total = sum((ord(c) - 48) * w for c, w in zip(body9, range(10, 1, -1), strict=True))
    check = 11 - total % 11
    if check == 11:
        return "0"
    if check == 10:
        return None
    return str(check)


_VERHOEFF_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_VERHOEFF_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)
_VERHOEFF_INV = (0, 4, 3, 2, 1, 5, 6, 7, 8, 9)


def verhoeff_ok(digits: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(digits)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][ord(ch) - 48]]
    return c == 0


def verhoeff_check_digit(body: str) -> str:
    c = 0
    for i, ch in enumerate(reversed(body)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[(i + 1) % 8][ord(ch) - 48]]
    return str(_VERHOEFF_INV[c])


# --------------------------------------------------------------------------------------------
# The verdict
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Verdict:
    #: The identifier itself is well formed: length, prefix or range, check digit(s).
    valid: bool
    #: ``ok``, else the first rule that failed validity, else why the tier stays below medium.
    reason: str
    #: ``none``, ``low`` or ``medium`` (the documented confidence tables; high adds a phrase).
    tier: str
    #: ``popular`` (the scheme's usual layout), ``irregular``, or ``none`` (nothing to lay out).
    layout: str
    #: The normalised identifier (digits; the compact IBAN), "" when there is none.
    normalised: str
    #: Brand (card), country (IBAN), or None.
    detail: str | None = None


def _none(reason: str, normalised: str = "", detail: str | None = None) -> Verdict:
    return Verdict(False, reason, "none", "none", normalised, detail)


def core_parts(s: str) -> tuple[str, str, str, str] | None:
    """``(core, char before, char after, digits)``: the core runs from the first to the last ASCII
    digit of ``s`` (which carries one boundary character on each side when known)."""
    idx = [i for i, c in enumerate(s) if c in DIGITS]
    if not idx:
        return None
    a, b = idx[0], idx[-1]
    return s[a : b + 1], s[:a][-1:], s[b + 1 :][:1], "".join(s[i] for i in idx)


def _only(core: str, delimiters: str) -> bool:
    return all(c in DIGITS or c in delimiters for c in core)


def _is_alpha(ch: str) -> bool:
    return ch.isalpha()


def _is_alnum(ch: str) -> bool:
    return ch.isalnum()


# --------------------------------------------------------------------------------------------
# Schemes
# --------------------------------------------------------------------------------------------


class Scheme:
    """One identifier format. Subclasses implement :meth:`check` and the generator hooks."""

    name: str = ""
    description: str = ""
    #: Delimiters allowed inside a value.
    delimiters: str = ""
    #: Perturbations this scheme can make.
    perturbations: tuple[str, ...] = ()
    #: Delimiter styles this scheme can lay a valid value out with.
    styles: tuple[str, ...] = ("none",)

    # --- checking -----------------------------------------------------------------------------

    def check(self, value: str, before: str = "", after: str = "", **opts: object) -> Verdict:
        raise NotImplementedError

    # --- generating ---------------------------------------------------------------------------

    def make_valid(self, rng: random.Random, **opts: object) -> tuple[str, str | None]:
        """A valid identifier (digits / compact form) and its detail."""
        raise NotImplementedError

    def groups(self, normalised: str) -> list[int]:
        """The popular layout's group sizes for this value."""
        return [len(normalised)]

    def layout(self, normalised: str, style: str) -> str:
        sep = STYLES[style]
        if not sep:
            return normalised
        out, i = [], 0
        for size in self.groups(normalised):
            out.append(normalised[i : i + size])
            i += size
        if i < len(normalised):
            out.append(normalised[i:])
        return sep.join(p for p in out if p)

    def bad_check(self, normalised: str, rng: random.Random) -> str:
        """The same identifier with a wrong check digit (last digit by default)."""
        last = normalised[-1]
        wrong = rng.choice([d for d in DIGITS if d != last])
        return normalised[:-1] + wrong

    def bad_prefix(self, rng: random.Random, **opts: object) -> tuple[str, str | None]:
        raise NotImplementedError

    def wrong_length(self, normalised: str, rng: random.Random) -> str:
        if rng.random() < 0.5 and len(normalised) > 2:
            cut = rng.randint(1, min(3, len(normalised) - 1))
            return normalised[:-cut]
        return normalised + "".join(rng.choice(DIGITS) for _ in range(rng.randint(1, 3)))


def _digit_layout_popular(core: str, groups_options: Sequence[Sequence[int]], delims: str) -> bool:
    """No delimiter, or ONE repeated delimiter at exactly one of the popular group layouts."""
    if all(c in DIGITS for c in core):
        return True
    seps = {c for c in core if c not in DIGITS}
    if len(seps) != 1:
        return False
    sep = next(iter(seps))
    if sep not in delims:
        return False
    parts = core.split(sep)
    return any([len(p) for p in parts] == list(g) for g in groups_options)


# --- payment card ---------------------------------------------------------------------------

#: Issuer ranges by brand: (prefix test on the digits, allowed lengths).
CARD_BRANDS: tuple[str, ...] = ("visa", "mastercard", "amex", "diners", "discover", "jcb")

#: BINs of the card networks' published TEST card numbers (processor sandbox documentation:
#: 4111 1111 1111 1111, 4242 4242 4242 4242, 5555 5555 5555 4444, 2223 0031 2200 3222,
#: 3782 822463 10005, 3714 496353 98431, 6011 1111 1111 1117, 3622 720627 1667,
#: 3566 0020 2036 0505 ...). A value drawn from these is still a random number in the range.
CARD_TEST_BINS: dict[str, tuple[str, ...]] = {
    "visa": ("411111", "424242", "400005"),
    "mastercard": ("555555", "222300", "520082"),
    "amex": ("378282", "371449"),
    "diners": ("362272", "305693"),
    "discover": ("601111", "601100"),
    "jcb": ("356600", "353011"),
}


def card_brand(d: str) -> str | None:
    """The brand whose issuer range AND length this PAN matches, else None."""
    n = len(d)
    if n < 2:
        return None
    p2 = int(d[:2])
    p3 = int(d[:3]) if n >= 3 else -1
    p4 = int(d[:4]) if n >= 4 else -1
    if d[0] == "4" and n in (13, 16, 19):
        return "visa"
    if (51 <= p2 <= 55 or 2221 <= p4 <= 2720) and n == 16:
        return "mastercard"
    if p2 in (34, 37) and n == 15:
        return "amex"
    if (p2 in (36, 38) or 300 <= p3 <= 305) and n == 14:
        return "diners"
    if (p4 == 6011 or 644 <= p3 <= 649 or p2 == 65) and 16 <= n <= 19:
        return "discover"
    if 3528 <= p4 <= 3589 and 16 <= n <= 19:
        return "jcb"
    return None


def card_prefix_known(d: str) -> bool:
    """Some brand's issuer range starts this PAN (length aside)."""
    if len(d) < 4:
        return False
    p2, p3, p4 = int(d[:2]), int(d[:3]), int(d[:4])
    return (
        d[0] == "4"
        or 51 <= p2 <= 55
        or 2221 <= p4 <= 2720
        or p2 in (34, 37, 36, 38, 65)
        or 300 <= p3 <= 305
        or p4 == 6011
        or 644 <= p3 <= 649
        or 3528 <= p4 <= 3589
    )


class CardPan(Scheme):
    name = "card_pan"
    description = "Payment card number (ISO/IEC 7812): issuer range and length, Luhn"
    delimiters = "-. /:"
    perturbations = (
        "bad_check_digit",
        "wrong_length",
        "mixed_delimiters",
        "adjacent_letters",
        "bad_prefix",
        "masked",
    )
    styles = ("none", "space", "dash", "dot", "slash", "colon")
    LENGTH = {"visa": 16, "mastercard": 16, "amex": 15, "diners": 14, "discover": 16, "jcb": 16}

    def check(self, value: str, before: str = "", after: str = "", **opts: object) -> Verdict:
        parts = core_parts(before[-1:] + value + after[:1])
        if parts is None:
            return _none("no_digits")
        core, pre, post, d = parts
        brand = card_brand(d)
        if not _only(core, self.delimiters):
            return _none("illegal_character", d)
        if not 12 <= len(d) <= 19:
            valid, reason = False, "wrong_length"
        elif not luhn_ok(d):
            valid, reason = False, "bad_check_digit"
        elif brand is None:
            valid, reason = False, ("wrong_length" if card_prefix_known(d) else "bad_prefix")
        else:
            valid, reason = True, "ok"
        popular = _digit_layout_popular(core, ((4, 4, 4, 4), (4, 6, 5), (4, 6, 4)), self.delimiters)
        layout = "popular" if popular else "irregular"
        if _is_alnum(pre) or _is_alnum(post):
            return Verdict(valid, "alphanumeric_boundary", "none", layout, d, brand)
        if not (12 <= len(d) <= 19 and luhn_ok(d)):
            return Verdict(valid, reason, "none", layout, d, brand)
        if popular and brand is not None:
            return Verdict(valid, "ok", "medium", layout, d, brand)
        return Verdict(valid, reason if not valid else "irregular_layout", "low", layout, d, brand)

    def groups(self, normalised: str) -> list[int]:
        n = len(normalised)
        if n == 15:
            return [4, 6, 5]
        if n == 14:
            return [4, 6, 4]
        return [4] * (n // 4) + ([n % 4] if n % 4 else [])

    def make_valid(self, rng: random.Random, **opts: object) -> tuple[str, str | None]:
        brands = list(opts.get("brands") or CARD_BRANDS)  # type: ignore[call-overload]
        brand = rng.choice(brands)
        length = self.LENGTH[brand]
        if opts.get("prefix_set") == "test":
            prefix = rng.choice(CARD_TEST_BINS[brand])
        else:
            prefix = self._issuer_prefix(brand, rng)
        body = prefix + "".join(rng.choice(DIGITS) for _ in range(length - 1 - len(prefix)))
        return body + luhn_check_digit(body), brand

    @staticmethod
    def _issuer_prefix(brand: str, rng: random.Random) -> str:
        if brand == "visa":
            return "4"
        if brand == "mastercard":
            return rng.choice([str(rng.randint(51, 55)), str(rng.randint(2221, 2720))])
        if brand == "amex":
            return rng.choice(["34", "37"])
        if brand == "diners":
            return rng.choice(["36", "38", str(rng.randint(300, 305))])
        if brand == "discover":
            return rng.choice(["6011", "65", str(rng.randint(644, 649))])
        return str(rng.randint(3528, 3589))

    def bad_prefix(self, rng: random.Random, **opts: object) -> tuple[str, str | None]:
        """A Luhn-valid 16-digit number no issuer range starts (first digit 0, 1, 7, 8 or 9)."""
        body = rng.choice("01789") + "".join(rng.choice(DIGITS) for _ in range(14))
        return body + luhn_check_digit(body), None


# --- US Social Security number ------------------------------------------------------------


def ssn_range_ok(d: str) -> bool:
    if len(d) != 9:
        return False
    area, group, serial = int(d[:3]), int(d[3:5]), int(d[5:])
    return area not in (0, 666) and area < 900 and group != 0 and serial != 0


class SsnUs(Scheme):
    name = "ssn_us"
    description = "US Social Security number: SSA area, group and serial rules (no check digit)"
    delimiters = "-. "
    perturbations = ("wrong_length", "mixed_delimiters", "adjacent_letters", "bad_range", "masked")
    styles = ("none", "space", "dash", "dot")

    def check(self, value: str, before: str = "", after: str = "", **opts: object) -> Verdict:
        parts = core_parts(before[-1:] + value + after[:1])
        if parts is None:
            return _none("no_digits")
        core, pre, post, d = parts
        if not _only(core, self.delimiters):
            return _none("illegal_character", d)
        if len(d) != 9:
            return _none("wrong_length", d)
        if not ssn_range_ok(d):
            return _none("bad_range", d)
        popular = core.isdigit() or (
            len(core) == 11
            and core[3] in self.delimiters
            and core[6] == core[3]
            and core.replace(core[3], "").isdigit()
        )
        layout = "popular" if popular else "irregular"
        if popular and not _is_alpha(pre) and not _is_alpha(post):
            return Verdict(True, "ok", "medium", layout, d)
        reason = "irregular_layout" if not popular else "letter_boundary"
        return Verdict(True, reason, "low", layout, d)

    def groups(self, normalised: str) -> list[int]:
        return [3, 2, 4]

    def make_valid(self, rng: random.Random, **opts: object) -> tuple[str, str | None]:
        area = rng.randint(1, 899)
        if area == 666:
            area = 667
        return f"{area:03d}{rng.randint(1, 99):02d}{rng.randint(1, 9999):04d}", None

    def bad_prefix(self, rng: random.Random, **opts: object) -> tuple[str, str | None]:
        """A number in a range the SSA never issues: area 000, 666 or 900-999 (including the
        advertising range 987-65-4320..4329), group 00, or serial 0000."""
        area = f"{rng.randint(1, 899):03d}".replace("666", "667")
        group = f"{rng.randint(1, 99):02d}"
        serial = f"{rng.randint(1, 9999):04d}"
        pick = rng.randrange(6)
        if pick == 0:
            return "000" + group + serial, "area_000"
        if pick == 1:
            return "666" + group + serial, "area_666"
        if pick == 2:
            return str(rng.randint(900, 999)) + group + serial, "area_9xx"
        if pick == 3:
            return f"98765432{rng.randint(0, 9)}", "advertising_range"
        if pick == 4:
            return area + "00" + serial, "group_00"
        return area + group + "0000", "serial_0000"

    def bad_check(self, normalised: str, rng: random.Random) -> str:  # pragma: no cover
        raise ValueError("ssn_us has no check digit")


# --- ABA routing number -------------------------------------------------------------------


def aba_prefix_ok(d: str) -> bool:
    p = int(d[:2])
    return p <= 12 or 21 <= p <= 32 or 61 <= p <= 72 or p == 80


ABA_PREFIXES = [*range(0, 13), *range(21, 33), *range(61, 73), 80]
ABA_BAD_PREFIXES = [p for p in range(100) if p not in ABA_PREFIXES]


class AbaRouting(Scheme):
    name = "aba_routing"
    description = "ABA routing transit number: Federal Reserve prefix, 3-7-1 check digit"
    delimiters = "-. "
    perturbations = (
        "bad_check_digit",
        "wrong_length",
        "mixed_delimiters",
        "adjacent_letters",
        "bad_prefix",
        "masked",
    )
    styles = ("none", "space", "dash", "dot")

    def check(self, value: str, before: str = "", after: str = "", **opts: object) -> Verdict:
        parts = core_parts(before[-1:] + value + after[:1])
        if parts is None:
            return _none("no_digits")
        core, pre, post, d = parts
        if not _only(core, self.delimiters):
            return _none("illegal_character", d)
        if len(d) != 9:
            return _none("wrong_length", d)
        if not aba_ok(d):
            return _none("bad_check_digit", d)
        if not aba_prefix_ok(d):
            return _none("bad_prefix", d)
        popular = core.isdigit()
        layout = "popular" if popular else "irregular"
        if popular and not _is_alpha(pre) and not _is_alpha(post):
            return Verdict(True, "ok", "medium", layout, d)
        return Verdict(
            True, "irregular_layout" if not popular else "letter_boundary", "low", layout, d
        )

    def groups(self, normalised: str) -> list[int]:
        return [3, 4, 2]

    def make_valid(self, rng: random.Random, **opts: object) -> tuple[str, str | None]:
        body = f"{rng.choice(ABA_PREFIXES):02d}" + "".join(rng.choice(DIGITS) for _ in range(6))
        return body + aba_check_digit(body), None

    def bad_prefix(self, rng: random.Random, **opts: object) -> tuple[str, str | None]:
        body = f"{rng.choice(ABA_BAD_PREFIXES):02d}" + "".join(rng.choice(DIGITS) for _ in range(6))
        return body + aba_check_digit(body), None


# --- IBAN -----------------------------------------------------------------------------------

#: ISO 13616 registry entries: country -> (IBAN length, BBAN format, English name). Format letters:
#: ``n`` digit, ``a`` upper-case letter, ``c`` letter or digit. The 45 countries of the Enhanced
#: IBAN - Europe dictionary (Tunisia and Israel included as the doc lists them).
IBAN_REGISTRY: dict[str, tuple[int, str, str]] = {
    "AD": (24, "4n4n12c", "Andorra"),
    "AT": (20, "5n11n", "Austria"),
    "BA": (20, "3n3n8n2n", "Bosnia and Herzegovina"),
    "BE": (16, "3n7n2n", "Belgium"),
    "BG": (22, "4a4n2n8c", "Bulgaria"),
    "CH": (21, "5n12c", "Switzerland"),
    "CY": (28, "3n5n16c", "Cyprus"),
    "CZ": (24, "4n6n10n", "Czechia"),
    "DE": (22, "8n10n", "Germany"),
    "DK": (18, "4n9n1n", "Denmark"),
    "EE": (20, "2n2n11n1n", "Estonia"),
    "ES": (24, "4n4n1n1n10n", "Spain"),
    "FI": (18, "3n11n", "Finland"),
    "FO": (18, "4n9n1n", "Faroe Islands"),
    "FR": (27, "5n5n11c2n", "France"),
    "GB": (22, "4a6n8n", "United Kingdom"),
    "GI": (23, "4a15c", "Gibraltar"),
    "GL": (18, "4n9n1n", "Greenland"),
    "GR": (27, "3n4n16c", "Greece"),
    "HR": (21, "7n10n", "Croatia"),
    "HU": (28, "3n4n1n15n1n", "Hungary"),
    "IE": (22, "4a6n8n", "Ireland"),
    "IL": (23, "3n3n13n", "Israel"),
    "IS": (26, "4n2n6n10n", "Iceland"),
    "IT": (27, "1a5n5n12c", "Italy"),
    "LI": (21, "5n12c", "Liechtenstein"),
    "LT": (20, "5n11n", "Lithuania"),
    "LU": (20, "3n13c", "Luxembourg"),
    "LV": (21, "4a13c", "Latvia"),
    "MC": (27, "5n5n11c2n", "Monaco"),
    "ME": (22, "3n13n2n", "Montenegro"),
    "MK": (19, "3n10c2n", "North Macedonia"),
    "MT": (31, "4a5n18c", "Malta"),
    "NL": (18, "4a10n", "Netherlands"),
    "NO": (15, "4n6n1n", "Norway"),
    "PL": (28, "8n16n", "Poland"),
    "PT": (25, "4n4n11n2n", "Portugal"),
    "RO": (24, "4a16c", "Romania"),
    "RS": (22, "3n13n2n", "Serbia"),
    "SE": (24, "3n16n1n", "Sweden"),
    "SI": (19, "5n8n2n", "Slovenia"),
    "SK": (24, "4n6n10n", "Slovakia"),
    "SM": (27, "1a5n5n12c", "San Marino"),
    "TN": (24, "2n3n13n2n", "Tunisia"),
    "TR": (26, "5n1n16c", "Turkey"),
}


def bban_pattern(fmt: str) -> str:
    """``4a6n8n`` -> ``aaaannnnnnnnnnnnnn`` (one letter per character)."""
    out, num = [], ""
    for ch in fmt:
        if ch.isdigit():
            num += ch
        else:
            out.append(ch * int(num))
            num = ""
    return "".join(out)


def bban_matches(bban: str, fmt: str) -> bool:
    pattern = bban_pattern(fmt)
    if len(bban) != len(pattern):
        return False
    for ch, kind in zip(bban, pattern, strict=True):
        if kind == "n" and ch not in DIGITS:
            return False
        if kind == "a" and ch not in UPPER:
            return False
        if kind == "c" and ch not in ALNUM_UPPER:
            return False
    return True


class Iban(Scheme):
    name = "iban"
    description = "IBAN (ISO 13616): listed country, its length and BBAN format, MOD 97-10"
    delimiters = " "
    perturbations = (
        "bad_check_digit",
        "wrong_length",
        "mixed_delimiters",
        "adjacent_letters",
        "bad_prefix",
        "masked",
    )
    styles = ("none", "space")

    @staticmethod
    def countries(opts: dict[str, object]) -> list[str]:
        chosen = opts.get("countries")
        return sorted(chosen) if chosen else sorted(IBAN_REGISTRY)  # type: ignore[call-overload]

    def check(self, value: str, before: str = "", after: str = "", **opts: object) -> Verdict:
        t = value.strip().replace(" ", "")
        if not t:
            return _none("no_characters")
        country = t[:2]
        layout = (
            "popular"
            if (" " not in value.strip() or value.strip().split(" ") == _quads(t))
            else "irregular"
        )
        if not all(c in ALNUM_UPPER for c in t):
            reason = (
                "not_uppercase"
                if all(c in ALNUM_UPPER for c in t.upper()) and t.isascii()
                else "illegal_character"
            )
            return Verdict(False, reason, "none", layout, t, None)
        listed = self.countries(dict(opts))
        if country not in listed:
            return Verdict(False, "bad_prefix", "none", layout, t, None)
        length, fmt, _ = IBAN_REGISTRY[country]
        if len(t) != length:
            return Verdict(False, "wrong_length", "none", layout, t, country)
        if not t[2:4].isdigit() or mod97(t) != 1:
            return Verdict(False, "bad_check_digit", "none", layout, t, country)
        if opts.get("check_bban", True) and not bban_matches(t[4:], fmt):
            return Verdict(False, "bad_bban_format", "none", layout, t, country)
        if _is_alnum(before[-1:]) or _is_alnum(after[:1]):
            return Verdict(True, "alphanumeric_boundary", "none", layout, t, country)
        return Verdict(True, "ok", "medium", layout, t, country)

    def groups(self, normalised: str) -> list[int]:
        n = len(normalised)
        return [4] * (n // 4) + ([n % 4] if n % 4 else [])

    def make_valid(self, rng: random.Random, **opts: object) -> tuple[str, str | None]:
        country = rng.choice(self.countries(dict(opts)))
        _, fmt, _ = IBAN_REGISTRY[country]
        bban = "".join(
            rng.choice({"n": DIGITS, "a": UPPER, "c": ALNUM_UPPER}[k]) for k in bban_pattern(fmt)
        )
        return country + iban_check_digits(country, bban) + bban, country

    def bad_check(self, normalised: str, rng: random.Random) -> str:
        right = normalised[2:4]
        wrong = rng.choice([f"{i:02d}" for i in range(2, 99) if f"{i:02d}" != right])
        return normalised[:2] + wrong + normalised[4:]

    def bad_prefix(self, rng: random.Random, **opts: object) -> tuple[str, str | None]:
        """A MOD-97-valid IBAN-shaped value whose country code is not in the configured list
        (an unlisted registry country when one exists, else a code no country uses)."""
        listed = set(self.countries(dict(opts)))
        others = sorted(c for c in IBAN_REGISTRY if c not in listed) or ["QX", "ZQ", "XQ"]
        country = rng.choice(others)
        if country in IBAN_REGISTRY:
            fmt = IBAN_REGISTRY[country][1]
            pattern = bban_pattern(fmt)
        else:
            pattern = "n" * 18
        bban = "".join(rng.choice({"n": DIGITS, "a": UPPER, "c": ALNUM_UPPER}[k]) for k in pattern)
        return country + iban_check_digits(country, bban) + bban, country

    def wrong_length(self, normalised: str, rng: random.Random) -> str:
        """Drop or add BBAN characters, then recompute the check digits: only the length is wrong."""
        country, bban = normalised[:2], normalised[4:]
        if rng.random() < 0.5:
            bban = bban[: -rng.randint(1, 3)]
        else:
            bban = bban + "".join(rng.choice(DIGITS) for _ in range(rng.randint(1, 3)))
        return country + iban_check_digits(country, bban) + bban


def _quads(t: str) -> list[str]:
    return [t[i : i + 4] for i in range(0, len(t), 4)]


# --- NHS number (Mod 11) ------------------------------------------------------------------


class Nhs(Scheme):
    name = "nhs"
    description = "NHS number (England, Wales, Isle of Man): 10 digits, Mod 11 check digit"
    delimiters = " -"
    perturbations = (
        "bad_check_digit",
        "wrong_length",
        "mixed_delimiters",
        "adjacent_letters",
        "masked",
    )
    styles = ("none", "space", "dash")

    def check(self, value: str, before: str = "", after: str = "", **opts: object) -> Verdict:
        parts = core_parts(before[-1:] + value + after[:1])
        if parts is None:
            return _none("no_digits")
        core, pre, post, d = parts
        if not _only(core, self.delimiters):
            return _none("illegal_character", d)
        if len(d) != 10:
            return _none("wrong_length", d)
        if nhs_check_digit(d[:9]) != d[9]:
            return _none("bad_check_digit", d)
        popular = _digit_layout_popular(core, ((3, 3, 4),), self.delimiters)
        layout = "popular" if popular else "irregular"
        if _is_alnum(pre) or _is_alnum(post):
            return Verdict(True, "alphanumeric_boundary", "none", layout, d)
        if popular:
            return Verdict(True, "ok", "medium", layout, d)
        return Verdict(True, "irregular_layout", "low", layout, d)

    def groups(self, normalised: str) -> list[int]:
        return [3, 3, 4]

    def make_valid(self, rng: random.Random, **opts: object) -> tuple[str, str | None]:
        while True:
            body = str(rng.randint(4, 7)) + "".join(rng.choice(DIGITS) for _ in range(8))
            check = nhs_check_digit(body)
            if check is not None:
                return body + check, None

    def bad_check(self, normalised: str, rng: random.Random) -> str:
        right = normalised[-1]
        return normalised[:-1] + rng.choice([d for d in DIGITS if d != right])


# --- Aadhaar (Verhoeff) -------------------------------------------------------------------


class Aadhaar(Scheme):
    name = "aadhaar"
    description = "Aadhaar number (India): 12 digits, first digit 2-9, Verhoeff check digit"
    delimiters = " -"
    perturbations = (
        "bad_check_digit",
        "wrong_length",
        "mixed_delimiters",
        "adjacent_letters",
        "bad_prefix",
        "masked",
    )
    styles = ("none", "space", "dash")

    def check(self, value: str, before: str = "", after: str = "", **opts: object) -> Verdict:
        parts = core_parts(before[-1:] + value + after[:1])
        if parts is None:
            return _none("no_digits")
        core, pre, post, d = parts
        if not _only(core, self.delimiters):
            return _none("illegal_character", d)
        if len(d) != 12:
            return _none("wrong_length", d)
        if not verhoeff_ok(d):
            return _none("bad_check_digit", d)
        if d[0] in "01":
            return _none("bad_prefix", d)
        popular = _digit_layout_popular(core, ((4, 4, 4),), self.delimiters)
        layout = "popular" if popular else "irregular"
        if _is_alnum(pre) or _is_alnum(post):
            return Verdict(True, "alphanumeric_boundary", "none", layout, d)
        if popular:
            return Verdict(True, "ok", "medium", layout, d)
        return Verdict(True, "irregular_layout", "low", layout, d)

    def groups(self, normalised: str) -> list[int]:
        return [4, 4, 4]

    def make_valid(self, rng: random.Random, **opts: object) -> tuple[str, str | None]:
        body = str(rng.randint(2, 9)) + "".join(rng.choice(DIGITS) for _ in range(10))
        return body + verhoeff_check_digit(body), None

    def bad_prefix(self, rng: random.Random, **opts: object) -> tuple[str, str | None]:
        body = rng.choice("01") + "".join(rng.choice(DIGITS) for _ in range(10))
        return body + verhoeff_check_digit(body), None


#: THE scheme registry: name -> scheme. One line per scheme.
SCHEMES: dict[str, Scheme] = {
    s.name: s for s in (CardPan(), SsnUs(), AbaRouting(), Iban(), Nhs(), Aadhaar())
}
SCHEME_NAMES: tuple[str, ...] = tuple(SCHEMES)


# --------------------------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------------------------


def row_rng(seed: int, scheme: str, row_key: str, occurrence: int) -> random.Random:
    """A generator for one row: a function of the step's seed and the row's identity only, so the
    output does not depend on batching or file order."""
    material = f"{int(seed)}\x1f{scheme}\x1f{row_key}\x1f{int(occurrence)}".encode()
    seed_int = int.from_bytes(hashlib.sha256(material).digest()[:8], "big")
    return random.Random(seed_int)  # noqa: S311 - reproducible synthetic data, not a secret


@dataclass(frozen=True)
class Generated:
    value: str
    perturbation: str
    valid: bool
    detail: str | None


#: Letters glued to a value by ``adjacent_letters``.
GLUE = ("x", "A", "cc", "Q", "no", "ID", "z")


def _mixed(normalised: str, delimiters: str, rng: random.Random) -> str:
    """Irregular groups joined by at least two DIFFERENT delimiter runs."""
    if len(delimiters) < 2:
        delimiters = delimiters + "-"
    cuts = sorted(rng.sample(range(1, len(normalised)), k=min(3, len(normalised) - 1)))
    pieces, last = [], 0
    for c in cuts:
        pieces.append(normalised[last:c])
        last = c
    pieces.append(normalised[last:])
    seps = [rng.choice(delimiters) * rng.randint(1, 3) for _ in range(len(pieces) - 1)]
    if len({s[0] for s in seps}) < 2 and len(seps) >= 2:
        first = seps[0][0]
        seps[-1] = rng.choice([d for d in delimiters if d != first]) * len(seps[-1])
    out = pieces[0]
    for sep, piece in zip(seps, pieces[1:], strict=True):
        out += sep + piece
    return out


def _mask(text: str, rng: random.Random) -> str:
    mask = rng.choice("X*")
    positions = [i for i, c in enumerate(text) if c.isalnum()]
    keep = set(positions[-4:])
    return "".join(mask if (i in positions and i not in keep) else c for i, c in enumerate(text))


def layout_wrong_length(scheme: Scheme, digits: str, style: str) -> str:
    """A wrong-length value laid out in ``style``: the scheme's popular groups, the LAST group
    taking the difference (``123-45-67890``, ``123-45-678``). :meth:`Scheme.layout` would put a
    longer value's surplus in a group of its own (``123-45-6789-0``), which holds a well-formed
    value a pattern reads; this never does."""
    sep = STYLES[style]
    if not sep:
        return digits
    parts: list[str] = []
    i = 0
    for size in scheme.groups(digits):
        if i >= len(digits):
            break
        parts.append(digits[i : i + size])
        i += size
    if i < len(digits):
        parts[-1] += digits[i:]
    return sep.join(parts)


def generate(
    scheme_name: str,
    rng: random.Random,
    *,
    perturbation: str,
    style: str,
    attempts: int = 50,
    wrong_length_in_style: bool = False,
    **opts: object,
) -> Generated:
    """One value. ``perturbation`` is ``none`` or one of the scheme's perturbations. The ``valid``
    flag is :meth:`Scheme.check`'s verdict on the value produced (never assumed); a draw whose
    verdict contradicts the perturbation's intent (a wrong length that happens to fit another
    brand) is drawn again, up to ``attempts`` times, then refused.

    ``wrong_length_in_style`` (``synth_identifier@1.1.0``, 017 pilot Q1): a ``wrong_length`` value
    is laid out in ``style`` (:func:`layout_wrong_length`) instead of with no delimiter. Off, the
    output is byte-identical to before: the flag changes no random draw, only the layout."""
    scheme = SCHEMES[scheme_name]
    sep_style = style
    for _ in range(attempts):
        normalised, detail = scheme.make_valid(rng, **opts)
        if perturbation == "none":
            value = scheme.layout(normalised, sep_style)
            want_valid = True
        elif perturbation == "bad_check_digit":
            value = scheme.layout(scheme.bad_check(normalised, rng), sep_style)
            want_valid = False
        elif perturbation == "wrong_length":
            wrong = scheme.wrong_length(normalised, rng)
            value = (
                layout_wrong_length(scheme, wrong, sep_style)
                if wrong_length_in_style
                else scheme.layout(wrong, "none")
            )
            want_valid = False
        elif perturbation == "mixed_delimiters":
            value = _mixed(normalised, scheme.delimiters, rng)
            want_valid = scheme.name != "iban"
        elif perturbation == "adjacent_letters":
            glue = rng.choice(GLUE)
            body = scheme.layout(normalised, sep_style)
            side = rng.randrange(3)
            value = glue + body if side == 0 else body + glue if side == 1 else glue + body + glue
            # A digit scheme reads the digits between the letters; an IBAN token has no such
            # core, so glued letters make the token itself invalid.
            want_valid = scheme.name != "iban"
        elif perturbation in ("bad_prefix", "bad_range"):
            normalised, detail = scheme.bad_prefix(rng, **opts)
            value = scheme.layout(normalised, sep_style)
            want_valid = False
        elif perturbation == "masked":
            value = _mask(scheme.layout(normalised, sep_style), rng)
            want_valid = False
        else:  # pragma: no cover - refused before generation
            raise ValueError(f"unknown perturbation {perturbation!r}")
        verdict = scheme.check(value, "", "", **opts)
        if verdict.valid == want_valid:
            return Generated(value, perturbation, verdict.valid, detail)
    raise ValueError(
        f"{scheme_name}: {attempts} draws of {perturbation!r} never produced a value whose "
        "verdict matched its intent"
    )


def supported_perturbations(scheme_name: str) -> tuple[str, ...]:
    return SCHEMES[scheme_name].perturbations


def best_tier(tiers: Sequence[str]) -> str:
    return max(tiers, key=lambda t: TIER_RANK.get(t, 0), default="none")
