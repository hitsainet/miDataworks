"""miWorkbench's scores answer names the two fields the truncated-row filter reads (021 FTASKS 20.4;
FR-021.33; P44).

A NAME pin, not the guarantee: the guarantee is miWorkbench's G5 cross-repo test, which drives its
real paged scores answer through miDataworks' real ``select_served_rows`` → ``join_rows`` →
``accept_source`` with the filter present and absent (R2B-5, P66). This reads miWorkbench's response
models by AST (``backend/src/schemas/probe_monitor.py``): the per-row ``truncated`` flag on
``ProbeScoreRow`` and ``reported_auroc_untruncated`` on ``ProbeEvaluationScores``, the class the
scores route returns. Red until miWorkbench's G5 push lands (suite ledger 1.8); recorded, not
weakened. Skips loudly without the sibling; fails under a require switch (``tests/support/siblings.py``).
"""

from __future__ import annotations

import ast

import pytest

from src.services.detector_sets.mistudio_scores import UNTRUNCATED_KEY
from tests.support.siblings import absent, sibling_repo

MODELS = {"ProbeScoreRow": "truncated", "ProbeEvaluationScores": UNTRUNCATED_KEY}


def _fields() -> dict[str, set[str]]:
    root = sibling_repo("miWorkbench")
    path = root / "backend" / "src" / "schemas" / "probe_monitor.py"
    if not path.is_file():
        absent(f"no miWorkbench schemas at {path} (set MIWORKBENCH_REPO)")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: dict[str, set[str]] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name in MODELS:
            out[node.name] = {
                stmt.target.id
                for stmt in node.body
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
            }
    return out


@pytest.mark.parametrize("model", sorted(MODELS))
def test_miworkbench_serves_the_truncation_fields(model: str) -> None:
    fields = _fields()
    assert model in fields, f"miWorkbench has no {model} in its probe_monitor schemas"
    assert (
        MODELS[model] in fields[model]
    ), f"miWorkbench's {model} has no {MODELS[model]!r} yet (its G5 push, suite ledger 1.8)"
