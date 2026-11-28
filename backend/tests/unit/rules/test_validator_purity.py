"""Validators are pure (FR-016.4; FTASKS 1.8).

Every validator case runs twice in this process, once in a fresh ``python -I`` process, and once with
``random``, ``time`` and ``datetime.now`` patched to raise: the verdicts are identical every time.
"""

from __future__ import annotations

import datetime as dt
import json
import random
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from src.rules import cases as rule_cases
from src.rules.validators import resolve

BACKEND = Path(__file__).resolve().parents[3]


def _verdicts() -> list[Any]:
    out = []
    for ref, section in rule_cases.load_cases()["validators"].items():
        vid, version = ref.rsplit("@", 1)
        validator = resolve(vid, int(version))
        for case in section["cases"]:
            out.append([case["id"], rule_cases.run_validator_case(validator, case).actual])
    return out


_SCRIPT = """
import json
from tests.unit.rules.test_validator_purity import _verdicts
print(json.dumps(_verdicts(), sort_keys=True))
"""


def test_two_runs_in_process_and_a_fresh_process_agree() -> None:
    first = json.dumps(_verdicts(), sort_keys=True)
    second = json.dumps(_verdicts(), sort_keys=True)
    fresh = subprocess.run(
        [sys.executable, "-I", "-c", "import sys; sys.path.insert(0, '.');" + _SCRIPT],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert first == second == fresh
    assert len(json.loads(first)) > 50


class _Forbidden(AssertionError):
    pass


def _raise(*_: Any, **__: Any) -> Any:
    raise _Forbidden("a validator read the clock or a random source")


def test_no_validator_reads_the_clock_or_randomness(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = json.dumps(_verdicts(), sort_keys=True)

    class _NoNow(dt.datetime):
        @classmethod
        def now(cls, tz: Any = None) -> Any:  # type: ignore[override]
            raise _Forbidden("datetime.now")

    monkeypatch.setattr(random, "random", _raise)
    monkeypatch.setattr(random.Random, "random", _raise)
    monkeypatch.setattr(time, "time", _raise)
    monkeypatch.setattr(time, "monotonic", _raise)
    monkeypatch.setattr(dt, "datetime", _NoNow)
    assert json.dumps(_verdicts(), sort_keys=True) == expected
