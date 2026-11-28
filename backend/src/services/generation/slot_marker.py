"""How a slot carrier's text is read for the run's slot token (017 FR-017.14; FU-7a, 2026-11-27).

A slot run's token is ``[[SLOT-<HEX>]]`` with a random eight-digit hex per run
(``run_service.new_slot_token``). A carrier must hold it exactly where its plan row has a slot,
once, and nowhere else.

Some generators copy the hex exactly but garble the word: on the FU-7a W4 pilot, Gemma's Q4_K_M
GGUF wrote ``[[SLT-…]]``, ``[[SL-…]]``, ``[[SL_…]]``, ``[[SLOF-…]]``, ``[[SL0T-…]]``,
``[[SLOTS-…]]`` and a dozen other forms, every one with the run's own hex inside its double square
brackets. The hex is random per run, so a bracketed marker that ends with exactly that hex can only
be a copy of the run's token: :func:`read` counts it as the slot and writes the canonical token in
its place (the record is ``generated`` and the run counts it as ``slot_garbled_repaired``). A
marker whose hex is wrong, changed in case or absent is never the slot; a carrier with no slot
left is discarded, as ``slot_corrupted`` when something marker-shaped is there (the hex in any
case, or a ``[[…]]`` token) and ``slot_missing`` when nothing is.

Only a run's NEW records are read this way. A completed run's records, counts and chunk files are
what the run wrote; nothing here re-reads them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: The token shape :func:`read` can repair; any other token is read exactly, as before.
TOKEN = re.compile(r"^\[\[SLOT-(?P<hex>[0-9A-F]{4,32})\]\]$")
#: The count key a repaired record adds to the run's counts (it is ``generated``: no reason code).
REPAIRED = "slot_garbled_repaired"
#: A word is letters and digits, optionally joined by underscores (``SL_OT``), at most 24 long.
_WORD = r"[A-Za-z0-9]{1,12}(?:_[A-Za-z0-9]{1,12})?"
#: A bracketed token of at most 40 characters on one line: something a model wrote as a marker.
_BRACKETED = re.compile(r"\[\[[^\[\]\n]{1,40}\]\]")


def _garbled(hex_: str) -> re.Pattern[str]:
    return re.compile(r"\[\[(?P<word>" + _WORD + r")[-_]" + re.escape(hex_) + r"\]\]")


@dataclass(frozen=True)
class SlotReading:
    """What a carrier's text holds. ``found`` counts the exact token plus every repairable copy;
    ``text`` has each repairable copy replaced by the canonical token; ``repaired_from`` lists the
    copies as written (empty when the text held only exact tokens)."""

    found: int
    text: str
    repaired_from: tuple[str, ...]
    marker_shaped: bool


def read(text: str | None, token: str | None) -> SlotReading:
    body = text or ""
    if not token:
        return SlotReading(0, body, (), False)
    exact = body.count(token)
    match = TOKEN.match(token)
    if match is None:
        return SlotReading(exact, body, (), exact > 0)
    hex_ = match.group("hex")
    copies: list[str] = []

    def canonical(m: re.Match[str]) -> str:
        if m.group(0) != token:
            copies.append(m.group(0))
        return token

    repaired = _garbled(hex_).sub(canonical, body)
    shaped = bool(_BRACKETED.search(body)) or hex_.lower() in body.lower()
    return SlotReading(exact + len(copies), repaired, tuple(copies), shaped)


def missing_reason(reading: SlotReading) -> str:
    """A carrier with fewer slots than its plan row: ``slot_corrupted`` when it holds something
    marker-shaped that is not the run's token, ``slot_missing`` when it holds nothing."""
    return "slot_corrupted" if reading.marker_shaped else "slot_missing"
