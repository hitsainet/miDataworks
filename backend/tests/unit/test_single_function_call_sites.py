"""One function per fact, asserted by AST rather than text search (task 2.6; FTID 002 section 8).

1. Every ``hashlib.sha256(...)`` CALL in ``src/services/`` and ``src/workers/`` sits in
   ``services/identity.py`` or ``services/row_keys.py``. A second hashing site is how two
   serialisers drift and a hash ends up describing bytes nobody wrote (miStudio 033).
2. Recipe hashing reaches ``canonical_json``, and so does manifest writing.

A source scrape matches comments and docstrings; the AST sees only calls.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
ALLOWED_HASHING = {SRC / "services" / "identity.py", SRC / "services" / "row_keys.py"}

#: Foundation's walking-skeleton self-check hashes text it just wrote to prove DuckDB read the
#: same rows back; it is a test of the read path, not a content address.
#: Feature 007's steering-set hash is miLLM's byte format (miLLM 028 FTDD section 5.3, X-07),
#: not a miDataworks content address: its bytes are defined by miLLM and pinned by TV-1..TV-4
#: (``tests/unit/generation/test_steering_hash_vectors.py``), so it cannot reuse canonical_json.
EXEMPT = {
    SRC / "workers" / "selftest_tasks.py",
    SRC / "services" / "generation" / "steering.py",
}


def _sha256_calls(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(), filename=str(path))
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "sha256"
            and isinstance(func.value, ast.Name)
            and func.value.id == "hashlib"
        ):
            lines.append(node.lineno)
        if isinstance(func, ast.Name) and func.id == "sha256":
            lines.append(node.lineno)
    return lines


def test_sha256_is_called_only_in_the_identity_modules() -> None:
    offenders: list[str] = []
    for folder in ("services", "workers"):
        for path in sorted((SRC / folder).rglob("*.py")):
            if path in ALLOWED_HASHING or path in EXEMPT:
                continue
            offenders.extend(f"{path.relative_to(SRC)}:{line}" for line in _sha256_calls(path))
    assert not offenders, f"hashlib.sha256 called outside identity.py / row_keys.py: {offenders}"


def test_the_identity_modules_do_hash() -> None:
    """The guard is not vacuous: the allowed modules really contain the calls it looks for."""
    assert _sha256_calls(SRC / "services" / "row_keys.py")
    assert _sha256_calls(SRC / "services" / "identity.py")


def _function(path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {path}")


def _called_names(fn: ast.FunctionDef) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
    return names


def test_recipe_hashing_calls_canonical_json() -> None:
    identity = SRC / "services" / "identity.py"
    assert "canonical_body" in _called_names(_function(identity, "recipe_hash"))
    assert "canonical_json" in _called_names(_function(identity, "canonical_body"))


def test_row_keys_call_canonical_json() -> None:
    assert "canonical_json" in _called_names(
        _function(SRC / "services" / "row_keys.py", "compute_row_key")
    )


def test_the_sha256_finder_sees_a_call_and_ignores_a_comment(tmp_path: Path) -> None:
    probe = tmp_path / "probe.py"
    probe.write_text("import hashlib\n# hashlib.sha256(b'x') in a comment\nhashlib.sha256(b'y')\n")
    assert _sha256_calls(probe) == [3]
