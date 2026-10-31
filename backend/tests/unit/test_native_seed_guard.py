"""No native operator draws from an unseeded random number generator (task 11.4; FR-002.30).

AST, not text: a call to ``random.<fn>``, ``np.random.<fn>`` (other than ``default_rng`` given the
step seed) or ``numpy.random.<fn>`` in ``src/operators/native/`` fails. Native operators arrive with
features 003 and 004; until then the folder is absent and the guard proves itself on a probe.
"""

from __future__ import annotations

import ast
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[2] / "src" / "operators" / "native"
SEEDED_FACTORIES = {"default_rng", "Generator", "PCG64", "SeedSequence"}


def unseeded_calls(source: str) -> list[int]:
    tree = ast.parse(source)
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        chain: list[str] = []
        target: ast.expr = node.func
        while isinstance(target, ast.Attribute):
            chain.append(target.attr)
            target = target.value
        if isinstance(target, ast.Name):
            chain.append(target.id)
        chain.reverse()
        if chain[:1] == ["random"] and len(chain) == 2 and chain[1] not in {"Random"}:
            lines.append(node.lineno)
        elif chain[:2] in (["np", "random"], ["numpy", "random"]):
            seeded = chain[-1] in SEEDED_FACTORIES and (node.args or node.keywords)
            if not seeded:
                lines.append(node.lineno)
        elif chain[:1] == ["random"] and chain[-1] == "Random" and not (node.args or node.keywords):
            lines.append(node.lineno)
    return lines


def test_no_native_operator_uses_an_unseeded_rng() -> None:
    offenders = [
        f"{path.name}:{line}"
        for path in sorted(NATIVE.rglob("*.py"))
        if NATIVE.exists()
        for line in unseeded_calls(path.read_text())
    ]
    assert not offenders, f"unseeded RNG in native operators: {offenders}"


def test_the_guard_detects_each_unseeded_form() -> None:
    bad = (
        "import random\nimport numpy as np\n"
        "random.random()\nrandom.shuffle(x)\nnp.random.rand(3)\nnp.random.default_rng()\n"
        "random.Random()\n"
    )
    assert unseeded_calls(bad) == [3, 4, 5, 6, 7]


def test_the_guard_accepts_seeded_forms() -> None:
    good = "import random\nimport numpy as np\nnp.random.default_rng(seed)\nrandom.Random(seed)\n"
    assert unseeded_calls(good) == []
