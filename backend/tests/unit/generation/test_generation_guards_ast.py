"""AST guards for feature 007 (FTASKS 3.9, 8.6; FTID 007 sections 2.5, 8).

1. The pure modules import only what keeps them pure.
2. No 007 module writes to miLLM: no non-GET request to a ``/api/...`` management path (profile
   create/patch/activate/deactivate/delete, SAE attach, model load), and no path literal naming
   those actions (FR-007.18, FR-007.30).
3. Each guard is CALLED at its named sites — judge independence at the API, the worker and 005's
   preflight; the held-out guards at the API and the worker; the steering check in the engine and
   the preview — and ``judge_conflicts`` is defined nowhere else (assert the call, not the text).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[3] / "src"
GEN = SRC / "services" / "generation"

PURE = {
    GEN / "rules.py": {"numpy", "collections", "dataclasses", "typing", "__future__"},
    GEN
    / "steering.py": {
        "hashlib",
        "math",
        "struct",
        "http_sfv",
        "collections",
        "dataclasses",
        "typing",
        "__future__",
    },
    GEN
    / "diversity_metrics.py": {"re", "numpy", "collections", "dataclasses", "typing", "__future__"},
}
#: Relative imports the pure modules may make (the one hashing module and each other).
PURE_RELATIVE = {GEN / "rules.py": {"identity", "steering"}}

MODULES_007 = [
    *sorted(GEN.glob("*.py")),
    SRC / "api" / "v1" / "endpoints" / "generation.py",
    SRC / "workers" / "generation_tasks.py",
    SRC / "workers" / "diversity_tasks.py",
    SRC / "operators" / "native" / "generation.py",
    SRC / "mcp_server" / "tools" / "generation.py",
]


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_pure_modules_import_only_what_keeps_them_pure() -> None:
    for path, allowed in PURE.items():
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] in allowed, f"{path.name} imports {alias.name}"
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    names = {a.name for a in node.names}
                    assert names <= PURE_RELATIVE.get(path, set()), f"{path.name}: {names}"
                else:
                    assert (node.module or "").split(".")[
                        0
                    ] in allowed, f"{path.name} imports from {node.module}"


#: A miLLM path segment that changes state (``/api/saes/attachments`` is a read and passes).
FORBIDDEN_SEGMENT = re.compile(r"/(activate|deactivate|attach|detach|load|unload|lease)(/|$)")


def test_no_007_module_writes_to_millm() -> None:
    offenders: list[str] = []
    for path in MODULES_007:
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value
                if text.startswith("/api/") and FORBIDDEN_SEGMENT.search(text):
                    offenders.append(f"{path.name}:{node.lineno} {text}")
            if not isinstance(node, ast.Call) or len(node.args) < 2:
                continue
            method, target = node.args[0], node.args[1]
            if not (isinstance(method, ast.Constant) and isinstance(method.value, str)):
                continue
            path_text = ast.unparse(target)
            if method.value.upper() in ("POST", "PUT", "PATCH", "DELETE") and "/api/" in path_text:
                offenders.append(f"{path.name}:{node.lineno} {method.value} {path_text}")
    assert not offenders, f"007 code must never change miLLM state: {offenders}"


def _calls_in(path: Path, function: str, callee: str) -> int:
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == function:
            return sum(
                1
                for sub in ast.walk(node)
                if isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == callee
                and isinstance(sub.func.value, ast.Name)
                and sub.func.value.id in ("rules", "steering", "seed_selection")
            )
    raise AssertionError(f"{path.name} has no function {function}")


CALL_SITES = [
    # judge independence: API, worker, 005 preflight
    (GEN / "run_service.py", "plan", "judge_conflicts"),
    (GEN / "run_engine.py", "_worker_independence", "judge_conflicts"),
    (GEN / "independence.py", "check_label_run", "judge_conflicts"),
    # held-out first and seed splits: API and worker
    (GEN / "run_service.py", "plan", "held_out_guard"),
    (GEN / "run_service.py", "plan", "seed_split_guard"),
    (GEN / "run_engine.py", "_run", "held_out_guard"),
    (GEN / "run_engine.py", "_run", "seed_split_guard"),
    # the per-row seed re-check: one implementation, called by the worker AND the preview
    (GEN / "seed_selection.py", "check_seed_rows", "row_is_eligible_seed"),
    (GEN / "run_engine.py", "_check_seed_rows", "check_seed_rows"),
    (GEN / "run_service.py", "_preview_rows", "check_seed_rows"),
    # seeds are chosen and rendered by one implementation for the run and the preview
    (GEN / "run_engine.py", "_seeds", "select_seed_rows"),
    (GEN / "run_service.py", "_preview_rows", "select_seed_rows"),
    (GEN / "run_engine.py", "_messages", "render_messages"),
    (GEN / "run_service.py", "_preview_sync", "render_messages"),
    # the steering check: every engine response (through decide) and the preview
    (GEN / "run_engine.py", "decide", "check_reported_state"),
    (GEN / "run_service.py", "_preview_sync", "check_reported_state"),
    # one axis: the plan and the compare route
    (GEN / "run_service.py", "plan", "one_axis_or_raise"),
    (GEN / "run_service.py", "compare", "one_axis_or_raise"),
]


def test_every_guard_is_called_at_its_named_sites() -> None:
    for path, function, callee in CALL_SITES:
        assert _calls_in(path, function, callee) >= 1, f"{path.name}:{function} must call {callee}"


def test_judge_conflicts_has_exactly_one_implementation() -> None:
    defined = [
        str(path)
        for path in SRC.rglob("*.py")
        for node in ast.walk(_tree(path))
        if isinstance(node, ast.FunctionDef) and node.name == "judge_conflicts"
    ]
    assert defined == [str(GEN / "rules.py")]


def test_the_engine_decides_every_outcome_in_one_place() -> None:
    """``"generated"`` is WRITTEN only by ``decide`` (comparisons elsewhere are reads), so a
    discarded response can never be turned into a generated record somewhere else."""
    path = GEN / "run_engine.py"
    sites = []
    for node in ast.walk(_tree(path)):
        if not isinstance(node, ast.FunctionDef):
            continue
        compared = {
            id(c) for sub in ast.walk(node) if isinstance(sub, ast.Compare) for c in ast.walk(sub)
        }
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Constant)
                and sub.value == "generated"
                and id(sub) not in compared
            ):
                sites.append(node.name)
    assert set(sites) == {"decide"}, sites


def test_a_pair_needs_both_sides_matched_not_only_generated() -> None:
    """Control C31: ``derive_pairs`` checks the steering match itself (defence in depth)."""
    from src.services.generation.record_store import GenRecord, derive_pairs

    def rec(index: int, side: str, check: str) -> GenRecord:
        return GenRecord(
            index,
            "respond",
            0,
            None,
            "s" * 64,
            "p" * 64,
            0,
            side,
            None,
            "m",
            None,
            None,
            None,
            check,
            (),
            1,
            None,
            1,
            "stop",
            "generated",
            None,
            "p",
            "t",
        )

    assert len(derive_pairs([rec(0, "a", "match"), rec(1, "b", "match")], "b")) == 1
    assert derive_pairs([rec(0, "a", "match"), rec(1, "b", "unreported")], "b") == []


def test_native_chat_generate_refuses_a_recipe_step() -> None:
    """Control C40: a build never calls a model (FR-002.2)."""
    import pyarrow as pa
    import pytest

    from src.operators.context import RunContext
    from src.operators.errors import OperatorError
    from src.operators.native.generation import NativeChatGenerate

    for kw in ({"step_execution_id": "x"}, {"sample": True}):
        ctx = RunContext(
            manifest=NativeChatGenerate.manifest,
            manifest_hash="0" * 64,
            step_seed=0,
            job_id=None,
            column_roles={"request": "content"},
            rowkey_scheme="dw.rowkey/v1",
            **kw,
        )
        with pytest.raises(OperatorError) as refused:
            NativeChatGenerate().run(pa.table({"_dw_row_key": ["k"], "request": ["{}"]}), {}, ctx)
        assert refused.value.code == "generation_stage_only"
