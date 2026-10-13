# Origin: miStudio (Onegaishimas/miStudio) backend/tests/unit/test_no_tautological_assertions.py @ c829a2cc
# Mode: adapt (docs/REUSE.md). Changes: scans every test tree (unit, integration, contract), not only tests/unit.
"""No assertion may be rescued by a disjunction.

WHY THIS FILE EXISTS. `assert X or True` asserts nothing, and miStudio found it
THREE separate times, in three different arcs:

  * `test_faithfulness_cancellation.py` — removed in an R1 round
  * `test_labeling_sweep_cancel.py`     — removed in an R1 round
  * `test_activation_mask.py`           — removed in the round-3 pass of the
    SAE data-path arc, where it guarded that arc's HEADLINE fix

Each time it was written as a hedge: the assertion was awkward, so a fallback
was bolted on, and the test then passed unconditionally for the rest of its
life. The suite stayed green over a defect in all three cases.

The rule is simple enough to enforce mechanically: if an assertion is awkward
enough to want a hedge, it is the wrong assertion.

Parsed, not grepped — a substring search would match the prose above, which is
precisely the mistake that made this file necessary.
"""

import ast
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parents[1]


def _tautological_asserts(path: Path):
    """Assertions whose test is an `or` with a constant-truthy operand."""
    try:
        tree = ast.parse(path.read_text())
    except SyntaxError:  # pragma: no cover - a broken file fails elsewhere
        return []

    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assert):
            continue
        test = node.test
        if not (isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or)):
            continue
        for operand in test.values:
            # `or True`, `or 1`, `or "x"` — anything constant and truthy makes
            # the whole assertion unconditional.
            if isinstance(operand, ast.Constant) and operand.value:
                found.append((node.lineno, ast.unparse(test)))
                break
    return found


def _test_files():
    return sorted(p for p in TESTS_DIR.rglob("test_*.py") if p.name != Path(__file__).name)


def test_no_assertion_is_unconditional():
    offenders = []
    for path in _test_files():
        for lineno, expr in _tautological_asserts(path):
            offenders.append(f"{path.relative_to(TESTS_DIR)}:{lineno}: assert {expr}")

    assert not offenders, (
        "these assertions can never fail, so the tests containing them assert "
        "nothing:\n  "
        + "\n  ".join(offenders)
        + "\n\nIf an assertion is awkward enough to want a hedge, it is the "
        "wrong assertion. This has been found three times in this repo."
    )


class TestTheDetectorItself:
    """A guard that cannot fire is the very thing this file is about."""

    def test_it_finds_or_true(self, tmp_path):
        f = tmp_path / "test_x.py"
        f.write_text("def test_a():\n    assert 'x' not in 'abc' or True\n")
        assert _tautological_asserts(f), "the detector missed `or True`"

    def test_it_finds_other_truthy_constants(self, tmp_path):
        f = tmp_path / "test_x.py"
        f.write_text("def test_a():\n    assert 0 or 1\n")
        assert _tautological_asserts(f)

    def test_it_does_not_flag_a_legitimate_or(self, tmp_path):
        """NEGATIVE CONTROL — `a or b` over real expressions is fine."""
        f = tmp_path / "test_x.py"
        f.write_text("def test_a():\n    assert len('ab') == 2 or len('ab') == 3\n")
        assert not _tautological_asserts(f)

    def test_it_does_not_flag_or_none_or_or_empty(self, tmp_path):
        """A FALSY constant does not rescue the assertion."""
        f = tmp_path / "test_x.py"
        f.write_text("def test_a():\n    assert False or 0\n")
        assert not _tautological_asserts(f)

    def test_it_scans_every_test_file(self):
        files = _test_files()
        assert len(files) > 20, "the scan is not reaching the suite"
        assert any("integration" in p.parts for p in files), "the scan must reach every test tree"
