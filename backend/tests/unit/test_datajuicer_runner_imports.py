"""The Data-Juicer runner imports only the standard library, Celery, pyarrow and Data-Juicer
(ADR-010; Foundation task 10.3). That image has none of the backend's dependencies and no
database credentials, so a ``src.*`` import would raise on its first task.

Walks the abstract syntax tree, so a module named in a docstring cannot trip or satisfy it.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.operators.datajuicer import runner

RUNNER = Path(runner.__file__)
ALLOWED_THIRD_PARTY = {"celery", "pyarrow", "data_juicer"}


def imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                roots.add("." * node.level + (node.module or ""))
            else:
                roots.add((node.module or "").split(".")[0])
    return roots


def disallowed(path: Path) -> set[str]:
    return {
        root
        for root in imported_roots(path)
        if root != "__future__"
        and root not in sys.stdlib_module_names
        and root not in ALLOWED_THIRD_PARTY
    }


def test_the_runner_imports_nothing_else() -> None:
    assert disallowed(RUNNER) == set(), f"runner imports {disallowed(RUNNER)}"


def test_the_guard_sees_a_src_import(tmp_path: Path) -> None:
    sample = tmp_path / "runner.py"
    sample.write_text(
        "import os\nfrom src.core.config import get_settings\nfrom ...core import storage\nimport numpy\n"
    )
    assert disallowed(sample) == {"src", "...core", "numpy"}


def test_the_guard_ignores_a_docstring(tmp_path: Path) -> None:
    sample = tmp_path / "runner.py"
    sample.write_text('"""from src.core import x"""\nimport json\n')
    assert disallowed(sample) == set()


def test_ping_copies_every_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    source = tmp_path / "in.parquet"
    pq.write_table(pa.table({"t": ["a", "b", "c"]}), source)
    out = tmp_path / "runs" / "job_1" / "out.parquet"
    assert runner.ping_copy(str(source), str(out)) == 3
    assert pq.read_table(out).column("t").to_pylist() == ["a", "b", "c"]
    assert list((tmp_path / "staging").iterdir()) == []


def test_ping_refuses_paths_outside_the_volume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    with pytest.raises(ValueError):
        runner.ping_copy("/etc/hosts", str(tmp_path / "data" / "x.parquet"))


def test_the_runner_consumes_only_the_datajuicer_queue() -> None:
    assert runner.app.conf.task_default_queue == "datajuicer"
    assert runner.PING_TASK in runner.app.tasks
    from src.core.celery_app import route_for

    assert route_for(runner.PING_TASK) == "datajuicer"
