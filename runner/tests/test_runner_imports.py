"""The runner imports only the allowed roots (FR-018.18 shape; FTASKS 4.7).

``src/runner`` may import the standard library, ``wasmtime``, ``starlette``, ``uvicorn``, ``anyio`` and
``src.runner_contract.{constants, prelude, reasons, hashing}`` plus ``src.core.canonical_json`` — and
nothing else from ``src`` (no services, models, config, database). A new ``from src.core import config``
turns this red.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
RUNNER_SRC = BACKEND / "src" / "runner"

ALLOWED_TOP = {"wasmtime", "starlette", "uvicorn", "anyio"}
ALLOWED_SRC = {
    "src.runner_contract.constants",
    "src.runner_contract.prelude",
    "src.runner_contract.reasons",
    "src.runner_contract.hashing",
    "src.runner_contract.text",
    "src.runner_contract.documents",
    "src.core.canonical_json",
}
STDLIB = set(sys.stdlib_module_names)


def _imports(path: Path) -> list[str]:
    out: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    pkg = "src.runner"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import within src.runner
                base = pkg if node.level == 1 else "src"
                out.append(f"{base}.{node.module}" if node.module else base)
            elif node.module:
                out.append(node.module)
    return out


def _offending(module: str) -> bool:
    top = module.split(".")[0]
    if top in STDLIB or top in ALLOWED_TOP:
        return False
    if module in ALLOWED_SRC:
        return False
    if module.startswith("src.runner.") or module == "src.runner":
        return False  # intra-package
    return module.startswith("src.") or top not in STDLIB


def test_the_runner_imports_only_allowed_modules() -> None:
    offenders: list[tuple[str, str]] = []
    for path in sorted(RUNNER_SRC.rglob("*.py")):
        for module in _imports(path):
            if (
                module.startswith("src.")
                and module not in ALLOWED_SRC
                and not module.startswith("src.runner")
            ):
                offenders.append((path.name, module))
    assert offenders == [], f"the runner must not import these: {offenders}"


def test_a_backend_import_would_be_caught() -> None:
    offenders = [
        m
        for m in [
            "src.core.config",
            "src.services.health_service",
            "src.models.code_rule",
        ]
        if m.startswith("src.") and m not in ALLOWED_SRC and not m.startswith("src.runner")
    ]
    assert offenders == [
        "src.core.config",
        "src.services.health_service",
        "src.models.code_rule",
    ]
