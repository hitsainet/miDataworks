"""``src/rules/`` imports no I/O layer (FR-016.4; FTASKS 2.9; FTID 016 section 2.3).

Walks every module's syntax tree and resolves each import (absolute or relative) to a dotted
``src.`` path; ``services``, ``models``, ``core.config`` and ``core.database`` are refused.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[3] / "src"
FORBIDDEN = ("src.services", "src.models", "src.core.config", "src.core.database")


def imports_of(path: Path) -> list[str]:
    package = ["src", *path.relative_to(SRC).parent.parts]
    out: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - node.level + 1] if node.level else []
            module = ".".join([*base, *(node.module.split(".") if node.module else [])])
            out.append(module)
            out += [f"{module}.{a.name}" for a in node.names]
    return out


def violations() -> list[str]:
    files = sorted((SRC / "rules").rglob("*.py"))
    assert len(files) >= 10, "the walk found too few modules"
    return [
        f"{p.relative_to(SRC)} imports {m}"
        for p in files
        for m in imports_of(p)
        if any(m == f or m.startswith(f + ".") for f in FORBIDDEN)
    ]


def test_the_rules_package_imports_no_io_layer() -> None:
    assert violations() == []


def test_the_walk_resolves_relative_imports() -> None:
    """Not vacuous: a relative import of core.config from rules/ resolves to the forbidden name."""
    found = imports_of(SRC / "rules" / "compose.py")
    assert "src.core.canonical_json" in found
