"""Designer queue isolation (ADR-010 amendment, 2026-10-07): the backend never imports
``data_designer``; the designer runner and its relay import nothing from the rest of ``src``.

Walks the abstract syntax tree, like the Data-Juicer guard.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

from tests.unit.test_datajuicer_runner_imports import imported_roots

SRC = Path(__file__).resolve().parents[2] / "src"
DESIGNER = SRC / "operators" / "data_designer"
RUNNER = DESIGNER / "runner.py"
RELAY = DESIGNER / "relay.py"
ALLOWED = {
    "celery",
    "pyarrow",
    "redis",
    "cryptography",
    "jsonschema",
    "data_designer",
    "httpx",
    "starlette",
    "uvicorn",
    "anyio",
    ".relay",
}


def _disallowed(path: Path) -> set[str]:
    return {
        root
        for root in imported_roots(path)
        if root != "__future__" and root not in sys.stdlib_module_names and root not in ALLOWED
    }


def test_the_runner_and_relay_import_nothing_from_the_backend() -> None:
    assert _disallowed(RUNNER) == set()
    assert _disallowed(RELAY) == set()


def test_no_backend_module_imports_data_designer() -> None:
    """Only the designer runner (which runs in its own image) may import the library."""
    offenders = []
    for path in SRC.rglob("*.py"):
        if path == RUNNER:
            continue
        if "data_designer" in imported_roots(path):
            offenders.append(str(path.relative_to(SRC)))
    assert offenders == []


def test_no_backend_module_imports_the_designer_runner() -> None:
    """Except tests, nothing loads the runner into a backend process (its tasks would register)."""
    offenders = []
    for path in SRC.rglob("*.py"):
        if path == RUNNER:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.endswith("runner"):
                if "data_designer" in (node.module or "") or (
                    path.parent == DESIGNER and node.level
                ):
                    offenders.append(str(path.relative_to(SRC)))
            if isinstance(node, ast.ImportFrom) and node.level and path.parent == DESIGNER:
                if any(alias.name == "runner" for alias in node.names):
                    offenders.append(str(path.relative_to(SRC)))
    assert offenders == []


def test_the_designer_tasks_are_not_registered_in_the_backend_app() -> None:
    from src.core.celery_app import celery_app, route_for

    celery_app.loader.import_default_modules()
    assert not {n for n in celery_app.tasks if n.startswith("midataworks.designer.")}
    for name in (
        "midataworks.designer.step",
        "midataworks.designer.preview",
        "midataworks.designer.ping",
    ):
        assert route_for(name) == "designer"


def test_the_runner_consumes_only_the_designer_queue() -> None:
    from src.operators.data_designer import runner

    assert runner.app.conf.task_default_queue == "designer"
    assert {runner.STEP_TASK, runner.PREVIEW_TASK, runner.PING_TASK} <= set(runner.app.tasks)
    assert runner.app.amqp.router.route({}, runner.STEP_TASK)["queue"].name == "designer"
