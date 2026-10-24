"""The published fuel floors follow their own published basis (FTASKS 2.10; Review R1).

``limits_floor_basis`` states how each fuel floor is derived from the published snapshot. These tests
re-derive the three fuel floors through the production host and compare, so the S0 measurement is
reproducible from the repository (not from a scratch script) and a floor that drifts from its basis
is red. Review R1 found ``load_fuel`` published at 2**24 while its basis (fixture 1's load fuel,
2,294,645, times ten) gives 2**25.

The values stay PROVISIONAL (ENH-006 D32) until the node Jobs (FTASKS 2.7) and the fuel-identity run
on GitHub's runners (2.9) confirm them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from src.runner import bundle as bundle_mod
from src.runner.host import Host, Limits, guest_request
from src.runner_contract.cases import load_cases
from src.runner_contract.text import replace_lone_surrogates

PACKAGED = (
    Path(__file__).resolve().parents[2]
    / "backend"
    / "src"
    / "schemas"
    / "data"
    / "midataworks-runner-v1.json"
)
CONTRACT: dict[str, Any] = json.loads(PACKAGED.read_text(encoding="utf-8"))
FLOOR = CONTRACT["limits_floor"]
CASES = load_cases()


def _pow2_at_least(n: int) -> int:
    return 1 << (n - 1).bit_length()


@pytest.fixture(scope="module")
def host(bundle_dir: Path) -> Any:
    engine = Host.engine_for(CONTRACT)
    loaded = bundle_mod.load(engine, bundle_dir, PACKAGED)
    assert loaded.module is not None, loaded.detail
    h = Host(engine, loaded.module, CONTRACT)
    yield h
    h.close()


def _limits() -> Limits:
    return Limits(
        call_fuel=1 << 34,
        load_fuel=1 << 30,
        prelude_fuel=1 << 30,
        memory_bytes=FLOOR["memory_bytes"],
        input_code_points=FLOOR["input_code_points"],
        output_bytes=FLOOR["output_bytes"],
        wall_ms=60_000,
    )


def _fuel(host: Host, doc: dict[str, Any], function: str, params: Any, text: str) -> dict[str, int]:
    request = guest_request(
        mode="run",
        source=doc["source"],
        function=function,
        params=params,
        text=text,
        diagnostics=False,
    )
    outcome = host.run_one(request, _limits())
    assert outcome.reduced.count is not None, outcome.reduced  # measured on a completed call
    return outcome.fuel_used


def test_the_basis_is_published_for_every_fuel_floor() -> None:
    basis = CONTRACT["limits_floor_basis"]
    assert "fixture 2" in basis["call_fuel"]
    assert "largest conformance document" in basis["load_fuel"]
    assert "maximal request" in basis["prelude_fuel"]


def test_load_fuel_is_ten_times_the_largest_conformance_load_rounded_up(host: Host) -> None:
    worst = 0
    for case in CASES["conformance"]:
        doc = CASES["documents"][case["doc"]]
        text = replace_lone_surrogates(case["text"])
        if case.get("expect", {}).get("count") is None:
            continue  # a reason case may end before its load completes
        worst = max(worst, _fuel(host, doc, case["function"], case["params"], text)["load"])
    assert worst > 2_000_000  # fixture 1 (Luhn): 2,294,645 on the published snapshot
    assert FLOOR["load_fuel"] == _pow2_at_least(10 * worst)


FIXTURE2_TEXTS = [
    "9" * 65_536,
    "9 " * 32_768,
    "99 " * 21_845 + "9",
    "\U0001f600" * 65_536,
    "1" * 65_536,
]


def test_call_fuel_is_ten_times_fixture_twos_worst_maximal_text_rounded_up(host: Host) -> None:
    doc = CASES["documents"]["fixture2_digit_sum"]
    worst = max(
        _fuel(host, doc, "digit_sum", {"sums": [3, 18]}, text)["call"] for text in FIXTURE2_TEXTS
    )
    assert FLOOR["call_fuel"] == _pow2_at_least(10 * worst)


def test_prelude_fuel_is_twice_a_maximal_request_rounded_up(host: Host) -> None:
    doc = CASES["documents"]["fixture2_digit_sum"]
    text = "\U0001f600" * FLOOR["input_code_points"]  # 65,536 four-byte code points
    used = _fuel(host, doc, "digit_sum", {"sums": [3, 18]}, text)["prelude"]
    assert FLOOR["prelude_fuel"] == _pow2_at_least(2 * used)
