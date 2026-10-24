"""RE2 and Python ``re.ASCII`` agree on the dialect, except at its listed divergences (Q35 (a)).

A seeded generator builds patterns inside the dialect and ASCII and mixed texts; matches and spans
must agree. The divergences (``$`` before a final newline, ``\\v`` in ``\\s``, Kelvin under
``ignore_case``, and iteration after an EMPTY match: the dialect resumes one code point later, where
Python may still return a non-empty match at the same position) are excluded from the generator or
the comparison and asserted separately. A failure prints the seed.
"""

from __future__ import annotations

import random
import re

from src.rules import dialect

SEED = 20261023
ATOMS = [
    "a",
    "b",
    "c",
    "x",
    "1",
    "7",
    r"\d",
    r"\w",
    r"\s",
    r"[a-c]",
    r"[^b]",
    r"[0-9]",
    ".",
    r"\.",
    "-",
    " ",
]
TEXT_CHARS = list("abcx17.- \t\n") + ["é", "５", "日", "B", "C"]


def _pattern(rng: random.Random, depth: int = 0) -> str:
    parts = []
    for _ in range(rng.randint(1, 4)):
        roll = rng.random()
        if roll < 0.15 and depth < 2:
            parts.append("(?:" + _pattern(rng, depth + 1) + "|" + _pattern(rng, depth + 1) + ")")
        else:
            parts.append(rng.choice(ATOMS))
        q = rng.random()
        if q < 0.15:
            parts.append(rng.choice(["*", "+", "?", "{1,3}", "{2}", "*?", "+?"]))
    body = "".join(parts)
    if depth == 0 and rng.random() < 0.15:
        body = "^" + body
    if depth == 0 and rng.random() < 0.1:
        body = r"\b" + body + r"\b"
    return body


def test_re2_and_python_ascii_agree_on_generated_patterns() -> None:
    rng = random.Random(SEED)
    for i in range(3000):
        pattern = _pattern(rng)
        text = "".join(rng.choice(TEXT_CHARS) for _ in range(rng.randint(0, 30)))
        assert dialect.scan(pattern) is None, (SEED, i, pattern)
        r2 = [
            (m.start(), m.end())
            for m in dialect.finditer(dialect.compile_pattern(pattern, False), text)
        ]
        py = [(m.start(), m.end()) for m in re.finditer(pattern, text, re.ASCII)]
        if r2 != py and any(a == b for a, b in r2 + py):
            continue  # the empty-match iteration divergence, asserted below
        assert r2 == py, f"seed {SEED} case {i}: {pattern!r} on {text!r}: re2 {r2} python {py}"


def test_the_empty_match_divergence() -> None:
    """After an empty match the dialect resumes one code point later (no second, non-empty match at
    the same position); Python returns both."""
    compiled = dialect.compile_pattern("(?:|ab)", False)
    assert [(m.start(), m.end()) for m in dialect.finditer(compiled, "ab")] == [
        (0, 0),
        (1, 1),
        (2, 2),
    ]
    assert [(m.start(), m.end()) for m in re.finditer("(?:|ab)", "ab", re.ASCII)] == [
        (0, 0),
        (0, 2),
        (2, 2),
    ]


def test_finditer_never_repeats_an_empty_match_after_multibyte_text() -> None:
    """The re2 binding's own finditer returns (1, 1) twice here (measured 2026-10-23)."""
    compiled = dialect.compile_pattern(r"\ba?\b", False)
    assert [(m.start(), m.end()) for m in dialect.finditer(compiled, "\u65e57x")] == [
        (1, 1),
        (3, 3),
    ]


def test_the_listed_divergences() -> None:
    assert re.search("a$", "a\n", re.ASCII) and not dialect.compile_pattern("a$", False).search(
        "a\n"
    )
    assert re.search(r"\s", "\v", re.ASCII) and not dialect.compile_pattern(r"\s", False).search(
        "\v"
    )
    assert not re.search("k", "K", re.ASCII | re.IGNORECASE)
    assert dialect.compile_pattern("k", True).search("K")
