"""The published floor says it is PROVISIONAL, machine-readably (ENH-006 D32, D47; Review R1 M4).

Consumers (miStudio 040, miLLM 039) vendor the contract file, so the status travels in the file:
``limits_floor_status.provisional`` stays true, naming what it waits for, until FTASKS 2.7 and 2.9
confirm the floor. The marker never blocks raising a floor (a contract minor).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.runner_contract import constants, document

BACKEND = Path(__file__).resolve().parents[3]
DOCS_COPY = BACKEND.parent / "docs" / "schemas" / document.CONTRACT_FILE_NAME


@pytest.mark.parametrize("path", [DOCS_COPY, document.PACKAGED_CONTRACT_PATH])
def test_the_published_file_marks_the_floor_provisional(path: Path) -> None:
    status = json.loads(path.read_text(encoding="utf-8"))["limits_floor_status"]
    assert status["provisional"] is True
    pending = " ".join(status["pending"])
    assert "2.7" in pending and "2.9" in pending
    assert "018_S0_measurement_2026-10-24.md" in status["measured"]
    assert "raised" in status["change_rule"].lower()


def test_the_rendered_contract_carries_the_marker() -> None:
    rendered = json.loads(document.render_contract_file())
    assert rendered["limits_floor_status"]["provisional"] is True
    # The floor itself stays a plain name -> int map: hosts iterate it as limits.
    assert all(type(v) is int for v in rendered["limits_floor"].values())


def test_a_provisional_floor_must_name_what_it_waits_for() -> None:
    bad = {**constants.LIMITS_FLOOR_STATUS, "pending": []}
    with pytest.raises(ValidationError, match="pending"):
        document.LimitsFloorStatus.model_validate(bad)


def test_a_confirmed_floor_waits_for_nothing() -> None:
    ok = {**constants.LIMITS_FLOOR_STATUS, "provisional": False, "pending": []}
    assert document.LimitsFloorStatus.model_validate(ok).provisional is False
    with pytest.raises(ValidationError, match="pending"):
        document.LimitsFloorStatus.model_validate({**ok, "pending": ["x"]})


def test_an_extra_status_field_is_refused() -> None:
    with pytest.raises(ValidationError):
        document.LimitsFloorStatus.model_validate({**constants.LIMITS_FLOOR_STATUS, "x": 1})


def test_raising_a_floor_still_renders(monkeypatch: pytest.MonkeyPatch) -> None:
    raised = constants.LIMITS_FLOOR["call_fuel"]
    assert raised is not None
    monkeypatch.setitem(constants.LIMITS_FLOOR, "call_fuel", raised * 2)
    rendered = json.loads(document.render_contract_file())
    assert rendered["limits_floor"]["call_fuel"] == raised * 2
    assert rendered["limits_floor_status"]["provisional"] is True
