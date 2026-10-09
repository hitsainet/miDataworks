"""The stored Hugging Face token is decrypted only in worker code (ADR-015; task 8.5).

Walks the abstract syntax tree of every module outside ``src/workers/`` (so a name in a comment or
docstring cannot satisfy or trip it) and fails on an import of ``src.workers.secrets`` or of
``resolve_hf_token``. No API route returns the token: the settings routes return masked views only.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
FORBIDDEN_MODULE = "workers.secrets"
FORBIDDEN_NAMES = {"resolve_hf_token", "resolve_endpoint_key"}


def offending_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = {alias.name for alias in node.names}
            if module.endswith(FORBIDDEN_MODULE) or (
                module.endswith("workers") and "secrets" in names
            ):
                found.append(f"{path.name}:{node.lineno} from {module}")
            elif names & FORBIDDEN_NAMES:
                found.append(f"{path.name}:{node.lineno} imports {sorted(names & FORBIDDEN_NAMES)}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.endswith(FORBIDDEN_MODULE):
                    found.append(f"{path.name}:{node.lineno} import {alias.name}")
    return found


def _non_worker_modules() -> list[Path]:
    return [p for p in SRC.rglob("*.py") if "workers" not in p.relative_to(SRC).parts]


def test_no_module_outside_workers_imports_the_token_resolver() -> None:
    offenders = [hit for path in _non_worker_modules() for hit in offending_imports(path)]
    assert offenders == [], f"the HF token resolver is imported outside workers/: {offenders}"


def test_the_scan_reaches_the_api_modules() -> None:
    names = {p.name for p in _non_worker_modules()}
    assert {"settings.py", "jobs.py", "approvals.py", "main.py"} <= names


def test_the_scanner_sees_a_forbidden_import(tmp_path: Path) -> None:
    sample = tmp_path / "route.py"
    sample.write_text(
        "from ...workers.secrets import resolve_hf_token\n"
        "from ...workers import secrets\n"
        "import src.workers.secrets\n"
    )
    assert len(offending_imports(sample)) == 3


def test_the_scanner_ignores_a_docstring_mention(tmp_path: Path) -> None:
    sample = tmp_path / "doc.py"
    sample.write_text('"""Never import resolve_hf_token from workers.secrets here."""\n')
    assert offending_imports(sample) == []


def test_the_resolver_lives_in_workers() -> None:
    from src.workers import secrets

    assert hasattr(secrets, "resolve_hf_token")
