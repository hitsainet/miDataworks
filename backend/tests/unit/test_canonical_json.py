"""The one canonical-JSON function (ADR-005; Foundation task 4.3)."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

from src.core.canonical_json import canonical_json, canonical_sha256, write_canonical_json

SRC = Path(__file__).resolve().parents[2] / "src"
FIXTURE = {"b": [3, 1, {"z": None, "a": True}], "a": "héllo", "n": 1.5, "i": -2}
PINNED = b'{"a":"h\xc3\xa9llo","b":[3,1,{"a":true,"z":null}],"i":-2,"n":1.5}'


def test_the_bytes_are_pinned() -> None:
    assert canonical_json(FIXTURE) == PINNED


def test_the_hash_is_sha256_of_those_bytes() -> None:
    assert canonical_sha256(FIXTURE) == hashlib.sha256(PINNED).hexdigest()


def test_key_order_does_not_change_the_bytes() -> None:
    assert canonical_json({"x": 1, "y": 2}) == canonical_json({"y": 2, "x": 1})


def test_nan_is_refused() -> None:
    with pytest.raises(ValueError):
        canonical_json({"x": float("nan")})


def test_written_bytes_equal_hashed_bytes(tmp_path: Path) -> None:
    path = tmp_path / "recipe.json"
    with open(path, "wb") as handle:
        write_canonical_json(handle, FIXTURE)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == canonical_sha256(FIXTURE)


def _calls_in(function_name: str) -> set[str]:
    tree = ast.parse((SRC / "core" / "canonical_json.py").read_text())
    fn = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == function_name
    )
    names: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, ast.Attribute):
                names.add(target.attr)
    return names


def test_hashing_and_writing_call_the_same_serialiser() -> None:
    """By abstract syntax tree, not text: both paths CALL canonical_json."""
    assert "canonical_json" in _calls_in("canonical_sha256")
    assert "canonical_json" in _calls_in("write_canonical_json")
    assert "dumps" not in _calls_in("canonical_sha256") | _calls_in("write_canonical_json")


def test_no_other_module_serialises_json_for_hashing() -> None:
    """A second serialiser feeding a digest is the defect this module exists to prevent."""
    offenders = []
    for path in SRC.rglob("*.py"):
        if path.name == "canonical_json.py":
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"sha256", "md5", "blake2b"}
                and any(
                    isinstance(arg, ast.Call)
                    and isinstance(arg.func, ast.Attribute)
                    and arg.func.attr == "dumps"
                    for arg in ast.walk(node)
                )
            ):
                offenders.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert offenders == [], f"json.dumps feeding a hash outside canonical_json: {offenders}"
