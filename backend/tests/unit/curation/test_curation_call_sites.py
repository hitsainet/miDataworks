"""Each decision function is called from exactly the production places named here, and the pure
modules import nothing that does I/O (FTASKS 14.7; FTID 004 §8, §14).

A test of a rule is then a test of what ships: a second, unpinned copy of the rule cannot appear
unnoticed, and the call cannot silently move. The CALL is found by walking the AST (a text search
would match the comments describing it).
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[3] / "src"

EXPECTED: dict[str, set[str]] = {
    "decide_warning": {"services/curation/api.py:evaluate_audit"},
    "balanced_accuracy_cv": {
        "services/curation/shortcut_rules.py:permuted_control",
        "services/curation/shortcut_rules.py:score_column",
    },
    "score_column": {"services/curation/audit_service.py:audit_table"},
    "cell_cap_plan": {"operators/native/curation/cell_balancer.py:run"},
    "plan_split": {"operators/native/curation/split.py:run"},
    "jaccard_estimate": {
        "services/curation/minhash.py:near_duplicate_groups",
        "operators/native/curation/dedup_minhash.py:compute_statistics",
        "operators/native/curation/dedup_minhash.py:run",
    },
}

PURE = (
    "shortcut_rules.py",
    "binning.py",
    "cells.py",
    "split_plan.py",
    "minhash.py",
    "text_stats.py",
    "trl_rules.py",
    "warning_copy.py",
)
ALLOWED_IMPORTS = {
    "__future__",
    "math",
    "hashlib",
    "dataclasses",
    "typing",
    "re",
    "collections",
    "collections.abc",
    "importlib",
    "numpy",
    "sklearn",
    "sklearn.metrics",
    "sklearn.model_selection",
}


def call_sites(name: str) -> set[str]:
    found: set[str] = set()
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func in ast.walk(tree):
            if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for node in ast.walk(func):
                if isinstance(node, ast.Call):
                    target = node.func
                    called = (
                        target.attr
                        if isinstance(target, ast.Attribute)
                        else getattr(target, "id", None)
                    )
                    if called == name:
                        found.add(f"{path.relative_to(SRC).as_posix()}:{func.name}")
    return found


def test_each_decision_has_exactly_its_production_callers() -> None:
    for name, expected in EXPECTED.items():
        assert call_sites(name) == expected, name


def test_the_call_site_scan_sees_calls() -> None:
    assert call_sites("record_progress"), "the AST walk must find a known call"


def test_pure_modules_import_nothing_with_io() -> None:
    for module in PURE:
        tree = ast.parse((SRC / "services/curation" / module).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # a relative import pulls in the package: only pure siblings
                    assert node.module in {m[:-3] for m in PURE}, (module, node.module)
                    continue
                names = {node.module or ""}
            else:
                continue
            assert names <= ALLOWED_IMPORTS, (module, names - ALLOWED_IMPORTS)
