"""The pattern dialect (FR-016.11; FTASKS 2.1 - 2.3).

The refusal cases and pattern cases of the contract file are run by
``tests/unit/contract/test_rule_signal_conformance.py``; this file holds the scanner's own unit
tests, the import rule and the scanner-gap list.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.rules import cases as rule_cases
from src.rules import dialect

SRC = Path(__file__).resolve().parents[3] / "src"


@pytest.mark.parametrize(
    ("pattern", "rule", "at"),
    [
        (r"x\1", "backreference", 1),
        (r"(?P<n>a)(?P=n)", "backreference", 8),
        (r"\k<n>", "backreference", 0),
        (r"ab(?=c)", "lookaround", 2),
        (r"(?!c)", "lookaround", 0),
        (r"(?<=c)d", "lookaround", 0),
        (r"x(?(1)a|b)", "conditional", 1),
        (r"(?>a)", "atomic", 0),
        (r"ab*+", "possessive", 2),
        (r"a{2}+", "possessive", 1),
        (r"(?s).", "inline_flag", 0),
        (r"(?i:a)", "inline_flag", 0),
        (r"[\p{L}]", "unicode_property", 1),
        (r"\PN", "unicode_property", 0),
        (r"ab\C", "any_byte", 2),
        (r"a{0,1001}", "repeat_too_large", 1),
        (r"((a{10}){10}){11}", "repeat_too_large", 13),
        ("é" * 2001, "too_long", 2000),
        (r"[a", "syntax", 0),
        (r"ab(c", "syntax", 2),
        (r"*a", "syntax", 0),
        (r"a**", "syntax", 2),
        (r"\b+", "syntax", 2),
        (r"$?", "syntax", 1),
        (r"[[:alpha:]]", "syntax", 1),
        (r"[\d-z]", "syntax", 1),
        (r"[a-\w]", "syntax", 3),
        (r"\x4", "syntax", 0),
        (r"(?<n>a)", "syntax", 0),
        (r"(?P<n>a)(?P<n>b)", "syntax", 8),
        (r"(?#note)", "syntax", 0),
        (r"\Qa\E", "syntax", 0),
    ],
)
def test_each_refusal_names_its_rule_and_code_point_position(
    pattern: str, rule: str, at: int
) -> None:
    refused = dialect.scan(pattern)
    assert refused is not None and (refused.rule, refused.position) == (rule, at)
    with pytest.raises(dialect.PatternRefused):
        dialect.compile_pattern(pattern, False)


def test_every_refusal_rule_is_reachable() -> None:
    assert set(dialect.REFUSAL_RULES) == {
        "backreference",
        "lookaround",
        "conditional",
        "atomic",
        "possessive",
        "inline_flag",
        "unicode_property",
        "any_byte",
        "repeat_too_large",
        "too_long",
        "syntax",
    }


@pytest.mark.parametrize(
    "pattern",
    [
        r"\d{3}-\d{2}-\d{4}",
        r"[^]a-c]",
        r"(?:a|b)+?",
        r"(?P<x>a)\b",
        r"x{1000}",
        r"[\x41-\x5a]",
        r"a{2,}",
    ],
)
def test_dialect_patterns_compile(pattern: str) -> None:
    assert dialect.scan(pattern) is None
    dialect.compile_pattern(pattern, True)


def test_the_scanner_gap_list_is_empty() -> None:
    """Every pattern any case names: the scanner passing it means RE2 compiles it (FTID 3.2)."""
    patterns: set[str] = set()
    data = rule_cases.load_cases()
    for doc in data["rule_documents"].values():
        patterns |= {s["pattern"] for s in doc["signals"] if s["kind"] == "pattern"}
    patterns |= {
        c["refuse"]["pattern"] for c in data["rule_kinds"]["refusals"] if "pattern" in c["refuse"]
    }
    gaps = []
    for pattern in sorted(patterns):
        if dialect.scan(pattern) is None:
            try:
                dialect.compile_pattern(pattern, False)
            except dialect.PatternRefused as exc:
                gaps.append((pattern, str(exc)))
    assert gaps == []
    assert len(patterns) > 10


# --- 2.2 the RE2 wrapper ---------------------------------------------------------------------------


def test_re2_is_imported_only_by_the_dialect_and_unconditionally() -> None:
    importers = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            )
            if any(n == "re2" or n.startswith("re2.") for n in names):
                importers.append(path.relative_to(SRC).as_posix())
    assert importers == ["rules/dialect.py"]
    tree = ast.parse((SRC / "rules" / "dialect.py").read_text(encoding="utf-8"))
    top_level = [n for n in tree.body if isinstance(n, ast.Import) and n.names[0].name == "re2"]
    assert top_level, "import re2 must be at module top"
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            caught = {ast.unparse(h.type) for h in node.handlers if h.type is not None}
            assert not any("ImportError" in c or "ModuleNotFoundError" in c for c in caught)


def test_kelvin_sign_matches_k_only_under_ignore_case() -> None:
    assert dialect.compile_pattern("k", True).search("K") is not None
    assert dialect.compile_pattern("k", False).search("K") is None


def test_dollar_does_not_match_before_a_final_newline() -> None:
    assert dialect.compile_pattern("a$", False).search("a\n") is None
    assert dialect.compile_pattern("a$", False).search("a") is not None
    assert dialect.compile_pattern("^b", False).search("a\nb") is None


def test_space_class_excludes_vertical_tab_and_digits_are_ascii() -> None:
    assert dialect.compile_pattern(r"\s", False).search("\v") is None
    assert dialect.compile_pattern(r"\d", False).search("５") is None
    assert dialect.compile_pattern(r"\w", False).search("é") is None


def test_compiled_patterns_are_cached() -> None:
    assert dialect.compile_pattern("cache-me", False) is dialect.compile_pattern("cache-me", False)


# --- 2.3 full-width folding -------------------------------------------------------------------------


def test_fold_is_one_to_one_and_only_with_the_option() -> None:
    text = "id １２３　Ａｂ"
    folded = dialect.fold_fullwidth_ascii(text)
    assert folded == "id 123 Ab"
    assert len(folded) == len(text)
    compiled = dialect.compile_pattern(r"\d+", False)
    assert compiled.search(text) is None
    m = compiled.search(folded)
    assert (
        m is not None and (m.start(), m.end()) == (3, 6) and text[m.start() : m.end()] == "１２３"
    )


def test_fold_table_covers_exactly_the_full_width_block() -> None:
    assert len(dialect.FULLWIDTH_TABLE) == 95
    assert all(chr(cp).isascii() for cp in dialect.FULLWIDTH_TABLE.values())
