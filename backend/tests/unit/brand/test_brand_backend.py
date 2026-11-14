"""Backend string literals (feature 020; FR-020.11, FR-020.23; FTASKS 8.5)."""

from __future__ import annotations

import ast
import io
import tokenize

import pytest

from tests.support import legacy_identifiers as li
from tests.unit.brand import scan


def _reference(export: li.Export, deny: str) -> int:
    """An independent read of the same files: ``tokenize`` STRING tokens (and f-string middles),
    docstrings dropped by their start line, evaluated to their text."""
    count = 0
    for path in scan.backend_files():
        source = path.read_text()
        tree = ast.parse(source)
        doc_lines = {
            n.body[0].value.lineno
            for n in ast.walk(tree)
            if isinstance(n, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
            and n.body
            and isinstance(n.body[0], ast.Expr)
            and isinstance(n.body[0].value, ast.Constant)
            and isinstance(n.body[0].value.value, str)
        }
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.STRING and token.start[0] not in doc_lines:
                try:
                    value = ast.literal_eval(token.string)
                except (ValueError, SyntaxError):
                    continue
                if isinstance(value, str):
                    count += len(li.classified_hits(value, export, deny)[0])
            elif token.type == getattr(tokenize, "FSTRING_MIDDLE", -1):
                count += len(li.classified_hits(token.string, export, deny)[0])
    return count


def test_backend_literals_report_count_equals_reference_grep() -> None:
    export, deny = li.load(), scan.policy()["deny"]
    assert len(scan.backend_files()) > 100, "backend/src was not listed"
    read, identifiers = scan.backend_hits(export, deny)
    scan.record("backend_literals_identifiers", identifiers)
    assert scan.record("backend_literals", read) == _reference(export, deny)


def test_backend_literals_have_no_unallowed_old_name() -> None:
    export, deny = li.load(), scan.policy()["deny"]
    scan.judge("backend_literals", scan.backend_hits(export, deny)[0])


def test_backend_identifiers_are_reported_and_are_identifiers() -> None:
    """The reported class is not a place to hide prose: every hit in it sits in a literal that is
    one identifier token, or in a code span that is one (the frozen names: wire codes, runtime
    keys, health keys, the task and class names)."""
    export, deny = li.load(), scan.policy()["deny"]
    for where, text in scan.backend_literals(scan.backend_files()):
        _, identifiers = li.classified_hits(text, export, deny)
        for hit in identifiers:
            tokens = [text, *li.CODE_SPAN.findall(text)]
            assert any(li.IDENTIFIER.fullmatch(t) and hit in t for t in tokens), (where, text)
            assert not any(t == hit and hit != hit.lower() for t in tokens), (where, text)


def test_backend_scanner_reads_every_file_with_a_string() -> None:
    """Control M56 survived at first: a scanner skipping a file with no unallowed hit keeps the
    same count. Coverage is checked per file: every file holding a non-docstring string token (an
    independent tokenize read) yields at least one literal from the scanner."""
    files = scan.backend_files()
    seen = {where.rsplit(":", 1)[0] for where, _ in scan.backend_literals(files)}
    expected = set()
    for path in files:
        source = path.read_text()
        tree = ast.parse(source)
        doc_lines = {
            n.body[0].value.lineno
            for n in ast.walk(tree)
            if isinstance(n, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
            and n.body
            and isinstance(n.body[0], ast.Expr)
            and isinstance(n.body[0].value, ast.Constant)
        }
        tokens = [
            tok
            for tok in tokenize.generate_tokens(io.StringIO(source).readline)
            if tok.type == tokenize.STRING and tok.start[0] not in doc_lines
        ]
        if tokens:
            expected.add(str(path.relative_to(scan.REPO)))
    assert len(expected) > 100
    assert expected - seen == set(), sorted(expected - seen)[:5]


def test_a_planted_literal_is_classified_through_the_scanner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Through ``scan.backend_hits``: prose is read, a whole identifier literal is reported."""
    export, deny = li.load(), scan.policy()["deny"]
    planted = [
        ("src/x.py:1", "Load it in miLLM."),
        ("src/x.py:2", "is_millm"),
        ("src/x.py:3", "miStudio"),
    ]
    monkeypatch.setattr(scan, "backend_literals", lambda paths: planted)
    read, identifiers = scan.backend_hits(export, deny)
    assert read == ["src/x.py:1: miLLM", "src/x.py:3: miStudio"]
    assert identifiers == ["src/x.py:2: millm"]
