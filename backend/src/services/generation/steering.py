"""The miLLM steering contract, consumer side (FR-007.19 – FR-007.21; FTDD 007 section 6.2; X-07).

PURE: the standard library and ``http_sfv`` only (``test_generation_pure_imports.py``). This is the
ONLY module in miDataworks that knows the ``X-miLLM-Steering`` grammar and the steering-set hash.

**The hash** (miLLM 028 FTDD section 5.3, FR-28.3.4; miLLM ``millm/core/steering_state.py``):

```
millm.steering-set/v1\\n
sae=<sae_id>\\n
<index>:<16 lowercase hex digits of the strength's big-endian binary64 bits>\\n   (per pair)
```

Zero strengths (``-0.0`` included) are dropped, pairs are sorted by index, and the hash covers
the APPLIED set — after miLLM clamps each strength to ±200 (``millm/core/steering_range.py``,
``STEERING_RANGE = 200.0``). A clamped side therefore reports a hash that differs from the hash
of what was sent, and ``clamped`` is what marks it a mismatch. TV-1 to TV-4 pin all of this
(``tests/unit/generation/test_steering_hash_vectors.py``).

**The header** is an RFC 8941 List; each member is a Token kind with parameters (miLLM 028 FTDD
section 5.2). Rules this module follows:

- the decision is on each member's KIND Token, never equality on the whole value:
  ``unknown;reason=read_failed`` is UNREPORTED, exactly as a missing header is;
- ``intensity`` is a String holding Python ``repr(float)``; it is compared as a parsed float;
- ``name`` is percent-encoded by miLLM and decoded here before comparing;
- ``features`` is an Integer COUNT of applied features, not the list; the list is in the hash;
- on a stream the same value arrives in the terminal chunk's ``millm_steering`` field.

A state miLLM did not report is never written as "unsteered", and a hash miLLM did not send is
never computed locally from what was asked for: :func:`check_reported_state` returns
``unreported``, and the caller records the header verbatim (``None`` when absent).
"""

from __future__ import annotations

import hashlib
import math
import struct
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from http_sfv.list import List as SfvList

CANONICAL_VERSION = "millm.steering-set/v1"
#: miLLM's apply-time clamp (``millm/core/steering_range.py``: ``STEERING_RANGE = 200.0``).
STEERING_RANGE = 200.0
#: The stream field that carries the header value (miLLM FR-28.3.9).
STREAM_FIELD = "millm_steering"

CheckKind = Literal["match", "mismatch", "unreported", "not_applicable"]

#: Machine reason codes a mismatch can carry (FTID 007 section 7.2).
MISMATCH_REASONS: tuple[str, ...] = (
    "kind",
    "name",
    "source",
    "intensity",
    "sae",
    "layer",
    "features",
    "hash",
    "changed",
    "clamped",
    "extra_items",
    "malformed",
)


# --- the hash -------------------------------------------------------------------------------


def clamp_strength(value: float) -> float:
    """miLLM's clamp: what the hook applies for a sent strength."""
    return max(-STEERING_RANGE, min(STEERING_RANGE, float(value)))


def applied_pairs(pairs: Iterable[tuple[int, float]]) -> dict[int, float]:
    """``index -> clamp(strength)`` with every zero (and ``-0.0``) removed: the applied set."""
    out: dict[int, float] = {}
    for index, strength in pairs:
        value = clamp_strength(strength)
        if value != 0.0:
            out[int(index)] = value
    return out


def clamped_count(pairs: Iterable[tuple[int, float]]) -> int:
    """How many strengths miLLM's clamp would change (the header's ``clamped``)."""
    return sum(1 for _, s in pairs if clamp_strength(s) != float(s))


def canonical_steering_form(sae_id: str, pairs: Iterable[tuple[int, float]]) -> bytes:
    """The canonical bytes of an APPLIED set (pass the applied values; zeros are dropped here).

    Refuses a line break in the SAE id (it could forge a feature line), a negative index and a
    non-finite strength, as miLLM does.
    """
    if "\n" in sae_id or "\r" in sae_id:
        raise ValueError(f"an SAE id must not contain a line break: {sae_id!r}")
    lines = [CANONICAL_VERSION, f"sae={sae_id}"]
    kept: dict[int, float] = {}
    for index, strength in pairs:
        value = float(strength)
        if not math.isfinite(value):
            raise ValueError(f"a non-finite strength cannot be hashed: {index}={value!r}")
        if int(index) < 0:
            raise ValueError(f"a feature index must be non-negative: {index}")
        if value == 0.0:
            continue
        kept[int(index)] = value
    for index in sorted(kept):
        lines.append(f"{index}:{struct.pack('>d', kept[index]).hex()}")
    return "".join(f"{line}\n" for line in lines).encode("utf-8")


def steering_set_hash(sae_id: str, pairs: Iterable[tuple[int, float]]) -> str:
    """``sha256:`` + 64 lowercase hex over :func:`canonical_steering_form` of the given pairs."""
    return "sha256:" + hashlib.sha256(canonical_steering_form(sae_id, pairs)).hexdigest()


def applied_set_hash(sae_id: str, sent: Iterable[tuple[int, float]]) -> str:
    """The hash miLLM reports for a SENT set: clamp first, then hash (TV-4)."""
    return steering_set_hash(sae_id, applied_pairs(sent).items())


# --- the header -----------------------------------------------------------------------------


def decode_name(value: str) -> str:
    """Undo miLLM's percent-encoding of a profile name (uppercase ``%XX`` over UTF-8 bytes)."""
    raw = bytearray()
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "%" and _is_hex(value[i + 1 : i + 3]):
            raw.append(int(value[i + 1 : i + 3], 16))
            i += 3
            continue
        raw.extend(ch.encode("utf-8"))
        i += 1
    return raw.decode("utf-8", errors="replace")


def _is_hex(text: str) -> bool:
    return len(text) == 2 and all(c in "0123456789abcdefABCDEF" for c in text)


@dataclass(frozen=True)
class HeaderItem:
    """One member: its kind and parameters, with ``name`` decoded and ``intensity`` parsed."""

    kind: str
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def intensity(self) -> float | None:
        raw = self.params.get("intensity")
        if raw is None:
            return None
        try:
            return float(str(raw))
        except ValueError:
            return None


@dataclass(frozen=True)
class Parsed:
    """The parsed header. ``missing`` when absent; ``malformed`` when it does not parse."""

    raw: str | None
    items: tuple[HeaderItem, ...] = ()
    missing: bool = False
    malformed: bool = False


def parse_steering_header(value: str | None) -> Parsed:
    """Parse ``X-miLLM-Steering`` (or the stream's ``millm_steering``) as an RFC 8941 List."""
    if value is None:
        return Parsed(None, missing=True)
    parsed = SfvList()
    try:
        parsed.parse(value.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        return Parsed(value, malformed=True)
    items: list[HeaderItem] = []
    for member in parsed:
        kind = str(getattr(member, "value", ""))
        params = {str(k): v for k, v in dict(getattr(member, "params", {}) or {}).items()}
        normalised: dict[str, Any] = {}
        for key, item in params.items():
            if isinstance(item, bool) or isinstance(item, int):
                normalised[key] = item
            else:
                normalised[key] = str(item)
        if "name" in normalised:
            normalised["name"] = decode_name(str(normalised["name"]))
        items.append(HeaderItem(kind, normalised))
    if not items:
        return Parsed(value, malformed=True)
    return Parsed(value, tuple(items))


# --- what was asked for ---------------------------------------------------------------------


@dataclass(frozen=True)
class Expected:
    """The requested steering of one side, as the snapshot recorded it.

    ``features`` holds the APPLIED pairs (after the clamp) the snapshot hashes; ``sent_clamped``
    is how many sent strengths miLLM's clamp changes (a non-zero value means every response
    reports ``clamped`` and is a mismatch, FTDD 007 section 6.2).
    """

    kind: Literal["none", "profile", "inline"]
    profile_name: str | None = None
    intensity: float | None = None
    sae_id: str | None = None
    layer: int | None = None
    features: tuple[tuple[int, float], ...] = ()
    set_hash: str | None = None


@dataclass(frozen=True)
class Check:
    result: CheckKind
    reasons: tuple[str, ...] = ()

    @property
    def matched(self) -> bool:
        return self.result == "match"

    def reason_code(self) -> str | None:
        """The record's ``reason_code`` for a discard (FTDD 007 section 4.1)."""
        if self.result == "mismatch":
            return "steering_mismatch"
        if self.result == "unreported":
            return "steering_unreported"
        return None


def _sae_mismatches(expected: Expected, item: HeaderItem) -> list[str]:
    reasons: list[str] = []
    if expected.sae_id is not None and item.params.get("sae") != expected.sae_id:
        reasons.append("sae")
    if expected.layer is not None and item.params.get("layer") != expected.layer:
        reasons.append("layer")
    count = item.params.get("features")
    if not isinstance(count, int) or isinstance(count, bool) or count != len(expected.features):
        reasons.append("features")
    if expected.set_hash is None or item.params.get("hash") != expected.set_hash:
        reasons.append("hash")
    return reasons


def check_reported_state(expected: Expected | None, parsed: Parsed) -> Check:
    """Compare what miLLM reports with what was asked for (FTDD 007 section 6.2).

    ``expected is None`` means the endpoint is not miLLM (no steering contract): the check does
    not apply and the record says so — never "unsteered".
    """
    if expected is None:
        return Check("not_applicable")
    if parsed.missing:
        return Check("unreported", ("missing",))
    if parsed.malformed:
        return Check("mismatch", ("malformed",))
    if any(item.kind == "unknown" for item in parsed.items):
        return Check("unreported", ("unknown",))
    if len(parsed.items) != 1:
        return Check("mismatch", ("extra_items",))
    item = parsed.items[0]
    reasons: list[str] = []
    if item.params.get("changed") is True:
        reasons.append("changed")
    clamped = item.params.get("clamped")
    if isinstance(clamped, int) and not isinstance(clamped, bool) and clamped >= 1:
        reasons.append("clamped")
    if item.kind != expected.kind:
        return Check("mismatch", ("kind", *reasons))
    if expected.kind == "none":
        return Check("mismatch", tuple(reasons)) if reasons else Check("match")
    if expected.kind == "profile":
        if item.params.get("name") != expected.profile_name:
            reasons.append("name")
        if item.params.get("source") != "request":
            reasons.append("source")
        if expected.intensity is None or item.intensity != float(expected.intensity):
            reasons.append("intensity")
    reasons.extend(_sae_mismatches(expected, item))
    return Check("mismatch", tuple(reasons)) if reasons else Check("match")


def check_header(expected: Expected | None, header: str | None) -> Check:
    """:func:`check_reported_state` over a raw header value."""
    return check_reported_state(expected, parse_steering_header(header))


def expected_from(
    kind: str,
    *,
    profile_name: str | None,
    intensity: float | None,
    sae_id: str | None,
    layer: int | None,
    features: Sequence[Sequence[float]] | Mapping[int, float],
    set_hash: str | None,
) -> Expected:
    """An :class:`Expected` from a snapshot's stored fields."""
    pairs = (
        tuple((int(i), float(s)) for i, s in features.items())
        if isinstance(features, Mapping)
        else tuple((int(p[0]), float(p[1])) for p in features)
    )
    assert kind in ("none", "profile", "inline")
    return Expected(
        kind,  # type: ignore[arg-type]
        profile_name=profile_name,
        intensity=intensity,
        sae_id=sae_id,
        layer=layer,
        features=pairs,
        set_hash=set_hash,
    )
