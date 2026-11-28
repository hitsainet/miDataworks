"""The pure layer imports only the standard library, NumPy, scikit-learn, SciPy and itself
(006 FTASKS 3.12; FTID 006 section 2.4). Walks each module's AST."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[3] / "src" / "services"
PURE = [
    "calibration/constants.py",
    "calibration/metrics.py",
    "calibration/ceiling.py",
    "calibration/checks.py",
    "calibration/registry.py",
    "calibration/verdict.py",
    "review/decision_rules.py",
    "review/sampling.py",
]
ALLOWED_THIRD_PARTY = {"numpy", "sklearn", "scipy"}


def imports_of(path: Path) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out += [(alias.name, 0) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            out.append((node.module or "", node.level))
    return out


@pytest.mark.parametrize("relative", PURE)
def test_pure_module_imports(relative: str) -> None:
    bad = []
    for name, level in imports_of(SRC / relative):
        if level:  # a relative import must stay inside the pure layer
            target = name.split(".")[0]
            if f"{Path(relative).parent}/{target}.py" not in PURE:
                bad.append(f".{name}")
            continue
        top = name.split(".")[0]
        if top in sys.stdlib_module_names or top == "__future__" or top in ALLOWED_THIRD_PARTY:
            continue
        bad.append(name)
    assert not bad, f"{relative} imports outside the pure layer: {bad}"
