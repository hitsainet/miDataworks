"""Plain tokens are read only in worker code (001 FTASKS 13.1; FTDD 8).

``resolve_hf_token`` (decrypts the stored token) and ``ephemeral_secrets.take`` (reads and deletes a
per-import token) may be reached only from ``src/workers/``; the API only ever ``put``s. Walks the
syntax tree, so a mention in a comment or docstring neither satisfies nor trips it.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
HOME = SRC / "core" / "ephemeral_secrets.py"


def take_uses(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("ephemeral_secrets"):
            if any(a.name in ("take", "*") for a in node.names):
                hits.append(f"{path.name}:{node.lineno} imports take")
        elif isinstance(node, ast.Attribute) and node.attr == "take":
            base = node.value
            if isinstance(base, ast.Name) and base.id == "ephemeral_secrets":
                hits.append(f"{path.name}:{node.lineno} calls ephemeral_secrets.take")
    return hits


def outside_workers() -> list[Path]:
    return [p for p in SRC.rglob("*.py") if "workers" not in p.relative_to(SRC).parts and p != HOME]


def test_take_is_reached_only_from_workers() -> None:
    offenders = [hit for path in outside_workers() for hit in take_uses(path)]
    assert offenders == [], offenders


def test_the_workers_really_take_the_token() -> None:
    """The guard watches a real call site: the import worker is where ``take`` happens."""
    assert take_uses(SRC / "workers" / "source_tasks.py"), "source_tasks no longer takes the token"


def test_the_api_puts_and_never_takes() -> None:
    api = (SRC / "api" / "v1" / "endpoints" / "sources.py").read_text()
    tree = ast.parse(api)
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "put" in attrs and take_uses(SRC / "api" / "v1" / "endpoints" / "sources.py") == []


def test_the_scanner_sees_a_forbidden_take(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text("from ..core import ephemeral_secrets\nephemeral_secrets.take('job')\n")
    assert take_uses(bad)
    bad.write_text("from ..core.ephemeral_secrets import take\n")
    assert take_uses(bad)
    bad.write_text('"""ephemeral_secrets.take is mentioned here only."""\n')
    assert take_uses(bad) == []
