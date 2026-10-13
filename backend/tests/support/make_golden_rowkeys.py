"""Independent implementation of ``dw.rowkey/v1`` that produced ``golden_rowkeys.json`` (task 2.2).

This script shares NO code with ``src``: it uses only ``hashlib``, ``unicodedata`` and a canonical
JSON serialiser written out by hand below, so the golden keys cannot agree with
``src/services/row_keys.py`` by construction (FTID 002 section 8, IQ2). The test compares the
committed file against the production function and never regenerates it.

Regenerate only when adding cases (never to make a failing test pass):

    cd backend && python -m tests.support.make_golden_rowkeys > tests/support/golden_rowkeys.json
"""

from __future__ import annotations

import hashlib
import sys
import unicodedata
from typing import Any

SCHEME = "dw.rowkey/v1"

_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def _string(text: str) -> str:
    out = ['"']
    for ch in text:
        if ch in _ESCAPES:
            out.append(_ESCAPES[ch])
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _dump(value: Any) -> str:
    """Sorted keys, no spaces, non-ASCII as itself: the JSON a canonical serialiser must emit."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, list):
        return "[" + ",".join(_dump(v) for v in value) + "]"
    if isinstance(value, dict):
        items = sorted(value.items())
        return "{" + ",".join(_string(k) + ":" + _dump(v) for k, v in items) + "}"
    raise TypeError(type(value))


def _norm(value: Any) -> Any:
    if isinstance(value, str):
        value = unicodedata.normalize("NFC", value)
        value = value.replace("\r\n", "\n").replace("\r", "\n")
        return value.strip()
    if isinstance(value, list):
        return [_norm(v) for v in value]
    if isinstance(value, dict):
        return {k: _norm(v) for k, v in value.items()}
    return value


def key(row: dict[str, Any], content_columns: list[str]) -> str:
    content = {c: _norm(row[c]) for c in sorted(content_columns)}
    payload = SCHEME + "\n" + _dump(content)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


#: (name, row, content columns). Pairs that must collide and pairs that must not are both here.
CASES: list[tuple[str, dict[str, Any], list[str]]] = [
    ("plain_text", {"text": "Why did the chicken cross the road?"}, ["text"]),
    ("plain_text_trailing_space", {"text": "Why did the chicken cross the road?  \n"}, ["text"]),
    ("crlf", {"text": "line one\r\nline two"}, ["text"]),
    ("cr_only", {"text": "line one\rline two"}, ["text"]),
    ("lf", {"text": "line one\nline two"}, ["text"]),
    ("nfc_composed", {"text": "café"}, ["text"]),
    ("nfd_decomposed", {"text": "café"}, ["text"]),
    ("inner_spacing_differs", {"text": "Trump 's tax plan"}, ["text"]),
    ("inner_spacing_reference", {"text": "Trump's tax plan"}, ["text"]),
    ("case_differs", {"text": "WHY did the chicken cross the road?"}, ["text"]),
    ("double_inner_space", {"text": "two  spaces"}, ["text"]),
    ("single_inner_space", {"text": "two spaces"}, ["text"]),
    ("non_ascii", {"text": "日本語のテキスト — dash"}, ["text"]),
    ("control_and_quote", {"text": 'say "hi"\tnow\\'}, ["text"]),
    (
        "chat_messages",
        {
            "messages": [
                {"role": "user", "content": "  Tell me a joke.\r\n"},
                {"role": "assistant", "content": "Why did the chicken cross the road?"},
            ]
        },
        ["messages"],
    ),
    (
        "chat_messages_normalised_equivalent",
        {
            "messages": [
                {"role": "user", "content": "Tell me a joke."},
                {"role": "assistant", "content": "Why did the chicken cross the road?"},
            ]
        },
        ["messages"],
    ),
    (
        "preference",
        {"prompt": "Pick one.", "chosen": "This one.", "rejected": "That one."},
        ["prompt", "chosen", "rejected"],
    ),
    (
        "preference_column_order_irrelevant",
        {"rejected": "That one.", "prompt": "Pick one.", "chosen": "This one."},
        ["rejected", "chosen", "prompt"],
    ),
    (
        "metadata_ignored",
        {"text": "Why did the chicken cross the road?", "source": "reddit", "score": 3},
        ["text"],
    ),
    ("integers_and_bools", {"text": "x", "label": 1, "flag": True}, ["text", "label", "flag"]),
    ("null_value", {"text": None}, ["text"]),
    ("float_value", {"text": "x", "score": 0.5}, ["text", "score"]),
]


def build() -> list[dict[str, Any]]:
    return [
        {"name": name, "row": row, "content_columns": cols, "key": key(row, cols)}
        for name, row, cols in CASES
    ]


if __name__ == "__main__":
    import json

    sys.stdout.write(json.dumps(build(), indent=2, ensure_ascii=False) + "\n")
