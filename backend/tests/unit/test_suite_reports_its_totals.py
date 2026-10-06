# Origin: miStudio (Onegaishimas/miStudio) backend/tests/unit/test_suite_reports_its_totals.py @ c829a2cc
# Mode: adapt (docs/REUSE.md). Changes: no --no-cov flag (coverage is not in addopts here); wording.
"""The suite must state its own totals, whatever flags it is run with.

In miStudio on 2026-09-07 three consecutive full backend runs produced output
containing no counts at all. `addopts` carried `-q` and the test command recorded in
CLAUDE.md added a second one; at `-qq` pytest drops its own `N passed in Xs`
line. The runs were green, but nothing in their output said so — and the
missing line was first blamed on the `[Emergency GPU Cleanup]` atexit handler,
which writes to stderr and was merely the last thing on screen.

A run that will not report what it counted cannot support "the suite is
green". These tests pin both halves of the fix: the doubled flag is gone, and
the totals line does not depend on the flag being gone.

MUTATION CONTROLS (each must turn this file red):
  * delete the `write_line` in conftest's pytest_terminal_summary
        -> "states its totals even under -qq" fails
  * put `-q` back in pyproject's addopts
        -> "addopts does not carry -q" fails
  * report only `failed` and drop `error` from the counts
        -> "counts collection errors, not just failures" fails
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]

# Written into tests/unit so the real tests/conftest.py applies to the inner
# run. A probe in a tmp_path directory would not pick up the conftest under
# test, and would prove nothing about how this suite actually behaves.
PROBE = Path(__file__).with_name(f"test_zz_totals_probe_{os.getpid()}.py")

PROBE_SOURCE = """
import pytest


def test_passes():
    assert True


def test_fails():
    assert False


@pytest.mark.skip(reason="probe")
def test_skips():
    pass
"""


def _run_probe(*flags: str) -> str:
    """Run pytest over a known pass/fail/skip probe and return its stdout."""
    PROBE.write_text(PROBE_SOURCE)
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(PROBE), "-p", "no:cacheprovider", *flags],
            cwd=BACKEND,
            capture_output=True,
            text=True,
            timeout=300,
        )
        return proc.stdout
    finally:
        PROBE.unlink(missing_ok=True)


@pytest.fixture(scope="module")
def qq_output() -> str:
    return _run_probe("-qq")


def test_states_its_totals_even_under_qq(qq_output):
    """-qq removes pytest's own summary. Ours must survive it."""
    assert "SUITE TOTALS:" in qq_output, (
        "no totals line under -qq; the run reports nothing it can be held to.\n"
        f"stdout was:\n{qq_output}"
    )


def test_the_totals_are_the_real_outcomes(qq_output):
    """A hardcoded line would pass the test above and mean nothing."""
    line = next(line for line in qq_output.splitlines() if "SUITE TOTALS:" in line)
    assert "1 passed" in line, line
    assert "1 failed" in line, line
    assert "1 skipped" in line, line


def test_counts_collection_errors_not_just_failures(qq_output):
    """An import error is a failure to RUN, and 'failed' alone would hide it.

    A suite whose fixtures blow up reports 0 failed; calling that green is the
    exact mistake this line exists to prevent.
    """
    line = next(line for line in qq_output.splitlines() if "SUITE TOTALS:" in line)
    assert re.search(r"\d+ errors", line), line


def test_addopts_does_not_carry_q():
    """Callers add `-q`; addopts adding another is what caused the outage.

    This is the source fix. The totals hook is the belt, this is the braces.
    """
    pyproject = (BACKEND / "pyproject.toml").read_text()
    addopts = re.search(r'^addopts\s*=\s*"([^"]*)"', pyproject, re.M)
    assert addopts, "no addopts line found in pyproject.toml"
    flags = addopts.group(1).split()
    assert "-q" not in flags and "--quiet" not in flags, (
        f"addopts carries a quiet flag ({addopts.group(1)!r}); a caller's own "
        "-q then makes -qq and the run stops reporting its counts"
    )
