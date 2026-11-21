"""Nothing in the backend imports the runner host (feature 018; FTASKS 6.10; FTID I4, X3).

The runner host (``src/runner/``) lives in its own image; the backend image excludes it
(``backend/.dockerignore``), so an import of it from anywhere else in ``src`` would work in the dev
tree and fail in the pod - or worse, tempt a caller to run code rules in the backend process. This
walks the AST of every module under ``src`` outside ``src/runner/`` and resolves relative imports,
so ``from ..runner import host`` is caught as surely as ``import src.runner.host``. The contract
package ``src.runner_contract`` is NOT the host and stays allowed. The walker is proven on canary
modules written in the same run.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
HOST_PACKAGE = "src.runner"


def module_name(path: Path, root: Path) -> str:
    rel = path.relative_to(root.parent).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _resolve(node: ast.ImportFrom, current: str, is_package: bool) -> str:
    if node.level == 0:
        return node.module or ""
    base = current.split(".") if is_package else current.split(".")[:-1]
    base = base[: len(base) - (node.level - 1)]
    return ".".join(base + ([node.module] if node.module else []))


def _is_host(name: str) -> bool:
    return name == HOST_PACKAGE or name.startswith(HOST_PACKAGE + ".")


def host_imports(path: Path, root: Path) -> list[str]:
    """Every import in ``path`` that reaches the runner host, as ``line: module``."""
    current = module_name(path, root)
    is_package = path.name == "__init__.py"
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found += [f"{node.lineno}: {a.name}" for a in node.names if _is_host(a.name)]
        elif isinstance(node, ast.ImportFrom):
            target = _resolve(node, current, is_package)
            if _is_host(target):
                found.append(f"{node.lineno}: {target}")
            elif target == "src":
                found += [f"{node.lineno}: src.{a.name}" for a in node.names if a.name == "runner"]
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
            value = node.args[0].value
            if isinstance(value, str) and _is_host(value):
                found.append(f"{node.lineno}: {value} (dynamic)")
    return found


def backend_modules(root: Path = SRC) -> list[Path]:
    return [p for p in sorted(root.rglob("*.py")) if "runner" != p.relative_to(root).parts[0]]


def test_no_backend_module_imports_the_runner_host() -> None:
    modules = backend_modules()
    assert len(modules) > 300, "the walk found too few modules to mean anything"
    offenders = {str(p.relative_to(SRC)): hits for p in modules if (hits := host_imports(p, SRC))}
    assert offenders == {}


def test_the_contract_package_is_not_the_host() -> None:
    assert any(
        "runner_contract" in line
        for p in backend_modules()
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.startswith(("from ", "import "))
    ), "something in the backend uses the contract package (control for the walk's scope)"


@pytest.mark.parametrize(
    ("relpath", "body"),
    [
        ("clients/canary.py", "from ..runner import host\n"),
        ("clients/canary.py", "from ..runner.host import Host\n"),
        ("services/code_rules/canary.py", "from ...runner import server\n"),
        ("canary.py", "import src.runner.server\n"),
        ("canary.py", "from src import runner\n"),
        ("canary.py", "import importlib\nimportlib.import_module('src.runner.host')\n"),
    ],
)
def test_a_canary_import_is_caught(tmp_path: Path, relpath: str, body: str) -> None:
    root = tmp_path / "src"
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    assert host_imports(path, root), body


def test_a_contract_import_is_not_a_canary(tmp_path: Path) -> None:
    root = tmp_path / "src"
    path = root / "clients" / "ok.py"
    path.parent.mkdir(parents=True)
    path.write_text("from ..runner_contract import hashing\nimport src.runner_contract\n")
    assert host_imports(path, root) == []
