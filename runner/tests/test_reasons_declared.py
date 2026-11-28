"""Every reason the host can emit is a contract reason (FR-018.7; FTASKS 4.8).

The host maps traps and faults through tables of ``Reason`` members; this reads those tables (not
source text) and asserts each value is in the contract. Adding an undeclared string to a table turns
it red.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.runner import host, wasi
from src.runner_contract.reasons import REASONS, Reason

PACKAGED = (
    Path(__file__).resolve().parents[2]
    / "backend"
    / "src"
    / "schemas"
    / "data"
    / "midataworks-runner-v1.json"
)


def _emitted() -> set[str]:
    emitted: set[Reason] = set()
    emitted |= set(host.TRAP_REASONS.values())
    emitted |= set(host.OUT_OF_FUEL_REASONS.values())
    emitted.add(host.OTHER_TRAP)
    for (
        value
    ) in wasi.NEXT_MARKER.values():  # markers are not reasons; touch the table so a rename shows
        assert isinstance(value, bytes)
    return {r.value for r in emitted}


def test_every_host_emitted_reason_is_declared() -> None:
    contract = set(json.loads(PACKAGED.read_text(encoding="utf-8"))["reasons"])
    assert _emitted() <= contract


def test_the_reason_enum_equals_the_contract_list() -> None:
    contract = json.loads(PACKAGED.read_text(encoding="utf-8"))["reasons"]
    assert list(REASONS) == contract


def test_an_undeclared_reason_string_would_be_caught() -> None:
    # If a table ever held a bare string outside Reason, _emitted() would include it and the subset
    # assertion would fail. Prove the guard bites.
    contract = set(json.loads(PACKAGED.read_text(encoding="utf-8"))["reasons"])
    polluted = _emitted() | {"rule_made_up"}
    assert not (polluted <= contract)
