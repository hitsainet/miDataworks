"""Where an element goes in real text (017 FR-017.22; FTID §3.4; TD-017.21).

Pure, over Unicode code points (Python ``str`` indices; never bytes). ``start`` and ``end`` are the
text's edges. ``middle`` is the boundary NEAREST the text's middle, searched in order of kind: a
line break, else a sentence mark (``. ! ?`` followed by whitespace, so a decimal point or a dot
inside a URL is no boundary; ``。 ！ ？`` anywhere), else whitespace; the kind found is recorded as
``boundary_kind``. A text with none of them takes its middle edge-free position
(``boundary_kind = "none"``: the element is inserted between two user-perceived characters, padded
by spaces; never inside a base character and its combining marks, a zero-width-joiner sequence, a
variation or skin-tone modifier, or a regional-indicator flag pair; Review R3). Ties go to the
earlier position, so the answer is deterministic.
"""

from __future__ import annotations

import unicodedata

SENTENCE_MARKS = frozenset(".!?。！？")
#: Marks that end a sentence only when whitespace follows (``3.14``, ``example.com`` do not).
SPACED_MARKS = frozenset(".!?")
ZWJ = "\u200d"
#: Characters that attach to the one before them (variation selectors, skin-tone modifiers).
_ATTACHING = frozenset({"\ufe0e", "\ufe0f", ZWJ, *(chr(c) for c in range(0x1F3FB, 0x1F400))})
EDGE = "edge"
LINE = "line"
SENTENCE = "sentence"
WHITESPACE = "whitespace"
NONE = "none"
SLOT = "slot"
KINDS: tuple[str, ...] = (SLOT, EDGE, LINE, SENTENCE, WHITESPACE, NONE)


def _nearest(positions: list[int], target: int) -> int | None:
    if not positions:
        return None
    return min(positions, key=lambda p: (abs(p - target), p))


def boundary(text: str, placement: str) -> tuple[int, str]:
    """``(position, boundary_kind)``: insert at ``position`` (0 <= position <= len(text))."""
    if placement == "start":
        return 0, EDGE
    if placement == "end":
        return len(text), EDGE
    if placement != "middle":
        raise ValueError(f"placement {placement!r} has no boundary in real text")
    target = len(text) // 2
    lines = [i + 1 for i, c in enumerate(text) if c == "\n" and 0 < i + 1 < len(text)]
    found = _nearest(lines, target)
    if found is not None:
        return found, LINE
    marks = [
        i + 1
        for i, c in enumerate(text)
        if c in SENTENCE_MARKS
        and 0 < i + 1 < len(text)
        and (c not in SPACED_MARKS or text[i + 1].isspace())
    ]
    found = _nearest(marks, target)
    if found is not None:
        return found, SENTENCE
    spaces = [i for i, c in enumerate(text) if c.isspace() and 0 < i < len(text)]
    found = _nearest(spaces, target)
    if found is not None:
        return found, WHITESPACE
    return _cluster_boundary(text, target), NONE


def _regional(c: str) -> bool:
    return 0x1F1E6 <= ord(c) <= 0x1F1FF


def _inside_cluster(text: str, i: int) -> bool:
    """Whether position ``i`` would split one user-perceived character."""
    if not 0 < i < len(text):
        return False
    here, before = text[i], text[i - 1]
    if unicodedata.combining(here) or unicodedata.category(here) in ("Mn", "Mc", "Me"):
        return True
    if here in _ATTACHING or before == ZWJ:
        return True
    if _regional(here) and _regional(before):
        run = 0
        j = i - 1
        while j >= 0 and _regional(text[j]):
            run += 1
            j -= 1
        return run % 2 == 1  # an odd run before: ``here`` completes a flag
    return False


def _cluster_boundary(text: str, target: int) -> int:
    """The nearest position at or after ``target`` (else before it) that splits no cluster."""
    forward = target
    while _inside_cluster(text, forward):
        forward += 1
    if forward < len(text):
        return forward
    backward = target
    while _inside_cluster(text, backward):
        backward -= 1
    return backward


def insert_at(text: str, position: int, piece: str) -> tuple[str, int]:
    """``text`` with ``piece`` inserted at ``position``, a space added on a side that would glue
    it to a word; returns the new text and where ``piece`` starts in it."""
    before, after = text[:position], text[position:]
    left = " " if before and not before[-1].isspace() else ""
    right = " " if after and not after[0].isspace() else ""
    return before + left + piece + right + after, len(before) + len(left)
