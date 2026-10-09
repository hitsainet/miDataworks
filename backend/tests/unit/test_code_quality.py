"""Ruff, Black and MyPy (strict) on ``src`` run as tests, so drift fails the suite (task 2.2)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
BIN = Path(sys.executable).parent


def _run(*command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=BACKEND, capture_output=True, text=True, timeout=600)


#: Probe files test_suite_reports_its_totals.py writes and deletes while other workers run.
PROBES = "test_zz_totals_probe_*"


def test_ruff_is_clean() -> None:
    result = _run(str(BIN / "ruff"), "check", "--extend-exclude", PROBES, "src", "tests")
    assert result.returncode == 0, result.stdout + result.stderr


def test_black_is_clean() -> None:
    result = _run(
        str(BIN / "black"),
        "--check",
        "--quiet",
        "--extend-exclude",
        "test_zz_totals_probe_",
        "src",
        "tests",
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_mypy_strict_is_clean() -> None:
    result = _run(str(BIN / "mypy"), "--no-incremental", "--cache-dir=/dev/null")
    assert result.returncode == 0, result.stdout + result.stderr
