"""The caller's half of the text rule (FR-018.5; contract ``text_rules``).

Every caller replaces lone surrogates (U+D800 - U+DFFF) with U+FFFD BEFORE encoding a text for the
runner; the runner refuses a text it cannot encode (``text_invalid``) rather than guessing. One place:
the runner client (S2) and the test harness both call :func:`replace_lone_surrogates` (M4).
Standard library only.
"""

from __future__ import annotations

import re

_LONE_SURROGATE = re.compile("[\ud800-\udfff]")
REPLACEMENT = "�"


def replace_lone_surrogates(text: str) -> str:
    """``text`` with every lone surrogate code point replaced by U+FFFD (nothing else changes)."""
    return _LONE_SURROGATE.sub(REPLACEMENT, text)
