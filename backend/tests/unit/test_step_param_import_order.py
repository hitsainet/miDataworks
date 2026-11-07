"""Review R4 (017): the step-parameter registrations hold whichever module is imported first.

``services/step_params.py`` imported 017's ``behaviour/input_config.py`` at its end, and that module
registers through ``rules/step_resolution.py``, which imports step_params: importing
step_resolution FIRST failed (a circular import), so ``pytest tests/integration/rules`` could not
collect. Each entry point runs in a fresh interpreter and the live registries are read.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
CHECK = """
import {module}
from src.services import step_params as sp
from src.services.rules import step_resolution as sr
assert {{"rule_set", "insert_element", "derive_labels", "slot_siblings"}} <= sr.PINNING_OPERATORS
assert {{"sample", "rule_set", "slot_siblings"}} <= set(sp.STEP_PARAM_RESOLVERS)
assert "slot_siblings" in sp.INPUT_PARAMS
"""


@pytest.mark.parametrize(
    "module",
    [
        "src.services.rules.step_resolution",
        "src.services.step_params",
        "src.services.behaviour.input_config",
        "src.main",
    ],
)
def test_each_entry_point_imports_and_registers(module: str) -> None:
    done = subprocess.run(
        [sys.executable, "-c", CHECK.format(module=module)],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr[-2000:]
