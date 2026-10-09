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
#: ``datasets`` added by feature 003 (FTID 003 I-2): Data-Juicer's operators take a
#: ``datasets.Dataset``, and it is already a Data-Juicer dependency in that image. ``src.*`` stays
#: refused.
#: ``jsonschema`` added too, pinned in the image: the worker re-validates params before the engine
#: sees them (FR-003.7, FTASKS 7.10).
ALLOWED_THIRD_PARTY = {"celery", "pyarrow", "data_juicer", "datasets", "jsonschema"}


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


def test_the_runner_consumes_only_the_datajuicer_queues() -> None:
    assert runner.app.conf.task_default_queue == "datajuicer"
    assert {runner.PING_TASK, runner.STEP_TASK, runner.PREVIEW_TASK} <= set(runner.app.tasks)
    from src.core.celery_app import route_for

    assert route_for(runner.PING_TASK) == "datajuicer"
    assert route_for(runner.STEP_TASK) == "datajuicer"
    assert route_for(runner.PREVIEW_TASK) == "datajuicer_preview"
    router = runner.app.amqp.router
    assert router.route({}, runner.PREVIEW_TASK)["queue"].name == "datajuicer_preview"
    assert router.route({}, runner.STEP_TASK)["queue"].name == "datajuicer"


def test_the_runner_tasks_are_not_registered_in_the_backend_app() -> None:
    """Queue isolation: the backend can SEND Data-Juicer work but never run it (ADR-010)."""
    from src.core.celery_app import celery_app

    celery_app.loader.import_default_modules()
    assert not {n for n in celery_app.tasks if n.startswith("midataworks.datajuicer.")}


def test_a_failed_step_writes_error_json_and_reraises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FTASKS 7.6: the traceback reaches the finaliser through error.json."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    (tmp_path / "in").mkdir()
    pq.write_table(
        pa.table({"text": ["a"], "_dw_row_key": ["k"], "_dw_occurrence": [0]}),
        tmp_path / "in" / "part-00000.parquet",
    )

    def explode(*_: object) -> None:
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(runner, "apply", explode)
    payload = {
        "input_dir": "in",
        "output_dir": "runs/j/s.dj",
        "op_name": "x",
        "kind": "filter",
        "params": {},
        "params_schema": {"type": "object"},
    }
    with pytest.raises(RuntimeError):
        runner.run_step(payload)
    error = (tmp_path / "runs" / "j" / "s.dj" / "error.json").read_text()
    assert "engine exploded" in error and "Traceback" in error
    assert not list((tmp_path / "staging").iterdir())


def test_a_step_publishes_output_and_decisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    (tmp_path / "in").mkdir()
    table = pa.table({"text": ["a", "bb"], "_dw_row_key": ["k1", "k2"], "_dw_occurrence": [0, 0]})
    pq.write_table(table, tmp_path / "in" / "part-00000.parquet")

    def keep_first(t: pa.Table, *_: object) -> tuple[pa.Table, pa.Table]:
        decisions = pa.table(
            {
                "row_key": ["k1", "k2"],
                "occurrence": pa.array([0, 0], pa.int32()),
                "keep": [True, False],
                "stats_json": ['{"len": 1}', '{"len": 2}'],
                "kept_key": pa.array([None, None], pa.string()),
            }
        )
        return t.slice(0, 1), decisions

    monkeypatch.setattr(runner, "apply", keep_first)
    payload = {
        "input_dir": "in",
        "output_dir": "runs/j/s.dj",
        "op_name": "x",
        "kind": "filter",
        "params": {},
        "params_schema": {"type": "object"},
    }
    assert runner.run_step(payload)["rows"] == 2
    out = tmp_path / "runs" / "j" / "s.dj"
    assert pq.read_table(out / "output.parquet").num_rows == 1
    assert pq.read_table(out / "decisions.parquet").column("keep").to_pylist() == [True, False]


def test_min_len_abc_is_refused_by_the_runner_before_the_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FTASKS 7.10: with the API check removed, the worker refuses before load_ops."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    engine_calls: list[object] = []
    monkeypatch.setattr(runner, "apply", lambda *a: engine_calls.append(a))
    payload = {
        "input_dir": "in",
        "output_dir": "runs/j/s.dj",
        "op_name": "text_length_filter",
        "kind": "filter",
        "params": {"min_len": "abc"},
        "params_schema": {
            "type": "object",
            "properties": {"min_len": {"type": "integer"}},
            "additionalProperties": False,
        },
    }
    with pytest.raises(runner.ParamsInvalid):
        runner.run_step(payload)
    assert engine_calls == []
    error = (tmp_path / "runs" / "j" / "s.dj" / "error.json").read_text()
    assert '"code": "params_invalid"' in error and "/min_len" in error
