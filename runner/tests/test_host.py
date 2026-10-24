"""Host-level controls: fresh instance, fuel phases, reply reduction, the marker protocol.

These exercise the production host directly (no HTTP). They carry the mutation controls M1, M2, M3,
M5, M6, M7, M8, and the once-in-order phase-marker protocol (ENH-006 D41; hostile classes 17-19,
whose guest-source escape vectors are the operator's open item — the protocol itself is proven here).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import wasmtime
from src.runner import wasi
from src.runner.host import Host, Limits, guest_request
from src.runner_contract.reasons import Reason

PACKAGED = (
    Path(__file__).resolve().parents[2]
    / "backend"
    / "src"
    / "schemas"
    / "data"
    / "midataworks-runner-v1.json"
)


@pytest.fixture(scope="module")
def host(bundle_dir: Path, contract: dict[str, Any]) -> Host:
    engine = Host.engine_for(contract)
    module = wasmtime.Module.from_file(engine, str(bundle_dir / "snapshot.wasm"))
    return Host(engine, module, contract)


def floor_limits(contract: dict[str, Any], **over: int) -> Limits:
    f = contract["limits_floor"]
    base = {
        "call_fuel": f["call_fuel"],
        "load_fuel": f["load_fuel"],
        "prelude_fuel": f["prelude_fuel"],
        "memory_bytes": f["memory_bytes"],
        "input_code_points": f["input_code_points"],
        "output_bytes": f["output_bytes"],
        "wall_ms": 10_000,
    }
    base.update(over)
    return Limits(**base)


def run(
    host: Host,
    contract: dict[str, Any],
    source: str,
    text: str,
    function: str = "f",
    params: dict | None = None,
    **over: int,
):
    req = guest_request(
        mode="run",
        source=source,
        function=function,
        params=params or {},
        text=text,
        diagnostics=False,
    )
    return host.run_one(req, floor_limits(contract, **over))


def test_fresh_instance_has_no_state_m3(host: Host, contract: dict[str, Any]) -> None:
    src = "c = [0]\ndef f(t, p):\n    c[0] += 1\n    return c[0]\n"
    counts = [run(host, contract, src, "x").reduced.count for _ in range(3)]
    assert counts == [1, 1, 1]  # M3: reusing one instance would give [1, 2, 3]


def test_fuel_edge_pair_m5(host: Host, contract: dict[str, Any]) -> None:
    ok = run(
        host, contract, "def f(t, p):\n    return sum(1 for _ in range(1000))\n", "x"
    )
    assert ok.reduced.count == 1000
    loop = run(host, contract, "def f(t, p):\n    while True:\n        pass\n", "x")
    assert (
        loop.reduced.reason is Reason.RULE_RESOURCE_LIMIT
    )  # NOT an edge: doubling the fuel leaves both green. M5 is pinned by the true edge pairs
    # conf.fuel_edge_at_floor / conf.fuel_edge_past_floor and limit.*.edge_* (Review R1).


def test_load_bomb_is_load_fuel_m6(host: Host, contract: dict[str, Any]) -> None:
    bomb = run(host, contract, "_x = 2 ** (1 << 24)\ndef f(t, p):\n    return 0\n", "x")
    assert bomb.reduced.reason is Reason.RULE_RESOURCE_LIMIT
    assert bomb.fuel_used["load"] >= bomb.fuel_used["call"]  # it died in the load phase


def test_reply_reduction_m1_m2_m7(host: Host, contract: dict[str, Any]) -> None:
    assert (
        run(host, contract, "def f(t, p):\n    return True\n", "x").reduced.reason
        is Reason.RULE_OUTPUT_INVALID
    )  # M1: bool not int
    assert (
        run(host, contract, "def f(t, p):\n    return 10 ** 9\n", "x").reduced.count
        == contract["count_cap"]
    )  # M2 clamp
    got = run(
        host, contract, "def f(t, p):\n    return t\n", "the matched text"
    ).reduced  # M7: raw reply never forwarded
    assert got.reason is Reason.RULE_OUTPUT_INVALID and got.count is None


def test_diagnostics_only_when_requested_m8(
    host: Host, contract: dict[str, Any]
) -> None:
    src = "def f(t, p):\n    raise ValueError('boom')\n"
    with_diag = host.run_one(
        guest_request(
            mode="run", source=src, function="f", params={}, text="x", diagnostics=True
        ),
        floor_limits(contract),
    )
    without = host.run_one(
        guest_request(
            mode="run", source=src, function="f", params={}, text="x", diagnostics=False
        ),
        floor_limits(contract),
    )
    assert (
        with_diag.reduced.reason is Reason.RULE_ERROR
        and with_diag.reduced.diag is not None
    )
    assert (
        without.reduced.reason is Reason.RULE_ERROR and without.reduced.diag is None
    )  # M8


def test_count_cap_is_respected(host: Host, contract: dict[str, Any]) -> None:
    assert (
        run(host, contract, "def f(t, p):\n    return 10 ** 18\n", "x").reduced.count
        == contract["count_cap"]
    )


# --- the phase-marker protocol (ENH-006 D41; hostile classes 17-19), at the host level -----------


def _call_marker_handler(
    contract: dict[str, Any], markers: list[bytes]
) -> wasi.CallState:
    """Drive the Table W fd_write handler's marker logic directly, with a fake Caller/Store."""
    state = wasi.CallState(
        stdin=b"",
        budgets={"prelude": 100, "load": 200, "call": 300},
        output_limit=4096,
        random_seed=b"x",
        final_phase="call",
    )

    class FakeStore:
        fuel = 100

    # wasi._marker reads/sets fuel through wasmtime.Store.get_fuel/set_fuel(caller); patch them.
    import src.runner.wasi as W

    real_get, real_set = wasmtime.Store.get_fuel, wasmtime.Store.set_fuel
    box = {"fuel": 100}
    wasmtime.Store.get_fuel = staticmethod(lambda caller: box["fuel"])  # type: ignore[assignment]
    wasmtime.Store.set_fuel = staticmethod(lambda caller, n: box.__setitem__("fuel", n))  # type: ignore[assignment]
    try:
        for m in markers:
            W._marker(object(), state, m)
    finally:
        wasmtime.Store.get_fuel, wasmtime.Store.set_fuel = real_get, real_set
    return state


def test_markers_accepted_once_and_in_order(contract: dict[str, Any]) -> None:
    state = _call_marker_handler(contract, [b"load", b"call"])
    assert state.phase == "call" and state.fault is None


def test_a_replayed_marker_is_refused_class_17(contract: dict[str, Any]) -> None:
    with pytest.raises(wasi.GuestEnd):
        _call_marker_handler(contract, [b"load", b"call", b"call"])


def test_an_out_of_order_marker_is_refused_class_18(contract: dict[str, Any]) -> None:
    with pytest.raises(wasi.GuestEnd):
        _call_marker_handler(contract, [b"call"])  # call before load


def test_an_unknown_marker_is_refused(contract: dict[str, Any]) -> None:
    with pytest.raises(wasi.GuestEnd):
        _call_marker_handler(contract, [b"reset"])


def test_a_second_reply_line_forging_is_rejected_class_19(
    host: Host, contract: dict[str, Any]
) -> None:
    # The host accepts exactly one reply line; a guest that wrote two would be rule_output_invalid.
    # Here we drive reply.reduce (the grammar barrier) directly with a forged second line.
    from src.runner import reply

    forged = b'{"phase":"call","result":{"t":"int","v":"1"}}\n{"phase":"call","result":{"t":"int","v":"999"}}\n'
    got = reply.reduce(forged, mode="run", output_limit=4096, count_cap=1000)
    assert got.reason is Reason.RULE_OUTPUT_INVALID
