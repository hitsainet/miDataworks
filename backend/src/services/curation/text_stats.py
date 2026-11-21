"""Text statistics for filters, the profile and contamination (FTDD 004 §6.1).

Pure. Lengths in characters and words (no tokens in v1, T-15); chat turns; repeated word n-gram
ratio; hashed word n-grams. A chat cell is a list of ``{role, content}`` messages; its text is the
messages' contents joined with newlines.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any

_WORD = re.compile(r"\w+", re.UNICODE)


def as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                content = item.get("content")
                parts.append(content if isinstance(content, str) else "")
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(value)


def char_length(value: Any) -> int:
    return len(as_text(value))


def words(value: Any) -> list[str]:
    return _WORD.findall(as_text(value).lower())


def word_length(value: Any) -> int:
    return len(words(value))


def turns(value: Any) -> int | None:
    """Number of messages in a chat cell; None when the cell is not a chat."""
    return len(value) if isinstance(value, list) else None


def turns_by_role(value: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    if isinstance(value, list):
        for item in value:
            role = str(item.get("role")) if isinstance(item, dict) else "?"
            out[role] = out.get(role, 0) + 1
    return out


def repeated_ngram_ratio(value: Any, n: int = 3) -> float:
    """Share of word n-grams that occur more than once (0 when the text has none)."""
    tokens = words(value)
    grams = [tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)]
    if not grams:
        return 0.0
    counts = Counter(grams)
    repeated = sum(c for c in counts.values() if c > 1)
    return repeated / len(grams)


def is_blank(value: Any) -> bool:
    return not as_text(value).strip()


def ngram_hash(gram: tuple[str, ...]) -> int:
    digest = hashlib.blake2b(" ".join(gram).encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") >> 1  # non-negative int64


def word_ngram_hashes(value: Any, n: int) -> list[int]:
    tokens = words(value)
    return [ngram_hash(tuple(tokens[i : i + n])) for i in range(len(tokens) - n + 1)]


def collapse_whitespace(value: Any) -> Any:
    """Inner-whitespace collapse for the optional comparison key (T-08); never stored."""
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, list):
        return [
            (
                {**m, "content": " ".join(m["content"].split())}
                if isinstance(m, dict) and isinstance(m.get("content"), str)
                else m
            )
            for m in value
        ]
    return value
