# Origin: miStudio (Onegaishimas/miStudio) backend/tests/unit/test_xdist_worker_isolation.py
# @ c829a2cc. Mode: adapt (docs/REUSE.md): one database variable (the sync URL is derived), plus
# DATA_DIR isolation and the guard that tests never touch a development or miStudio database.
"""Parallel runs isolate per worker (Foundation task 11.3)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.support import xdist_isolation as iso

CONFTEST = Path(__file__).resolve().parents[1] / "conftest.py"


class TestTheWorkerGetsItsOwnDatabaseAndVolume:
    def test_serial_changes_nothing(self) -> None:
        assert (
            iso.plan_worker_environment({"DATABASE_URL": "postgresql+asyncpg://h/midataworks_test"})
            == {}
        )

    def test_the_database_and_data_dir_are_suffixed(self) -> None:
        plan = iso.plan_worker_environment(
            {
                "PYTEST_XDIST_WORKER": "gw3",
                "DATABASE_URL": "postgresql+asyncpg://h/midataworks_test",
                "DATA_DIR": "/tmp/dw",
            }
        )
        assert plan["DATABASE_URL"] == "postgresql+asyncpg://h/midataworks_test_gw3"
        assert plan["DATA_DIR"] == "/tmp/dw_gw3"

    def test_applying_it_twice_does_not_stack_suffixes(self) -> None:
        once = iso.worker_database_url("postgresql://h/midataworks_test", "gw2")
        assert iso.worker_database_url(once, "gw2") == once

    def test_the_libpq_dsn_drops_the_driver(self) -> None:
        assert iso.libpq_dsn("postgresql+asyncpg://u:p@h:1/d") == "postgresql://u:p@h:1/d"

    @pytest.mark.parametrize("var", iso.THREAD_VARS)
    def test_threads_are_pinned_under_xdist(self, var: str) -> None:
        assert iso.plan_worker_environment({"PYTEST_XDIST_WORKER": "gw0"})[var] == "1"


class TestItRunsBeforeSettingsAreRead:
    def test_the_isolation_call_precedes_every_src_import(self) -> None:
        tree = ast.parse(CONFTEST.read_text())
        calls = [
            n.lineno
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_isolate_xdist_worker"
        ]
        src_imports = [
            n.lineno
            for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom) and (n.module or "").startswith("src")
        ]
        assert calls and src_imports
        assert min(calls) < min(src_imports)


def test_tests_never_run_against_a_non_test_database() -> None:
    import os

    name = os.environ["DATABASE_URL"].rsplit("/", 1)[-1]
    assert "test" in name and not name.startswith("mistudio")
