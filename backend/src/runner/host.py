"""The Wasmtime host: one engine, one compiled snapshot, a fresh instance per call (FTDD §4.4, §7.2).

``Host.run_one`` is the only place a guest runs: a NEW ``Store`` and instance of the pre-initialised
snapshot for every (text, function) — never cached (``instance_rule``; M3). Three fuel budgets on one
store (prelude, load, call), switched by the once-in-order markers of Table W (``wasi.py``); memory
capped by ``Store.set_limits``; wall time by the epoch deadline of an engine-wide 10 ms ticker. Traps
map to contract reasons here; replies are reduced by ``reply.reduce``.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from typing import Any

import wasmtime

from src.core.canonical_json import canonical_json
from src.runner_contract.reasons import Reason

from . import reply, wasi

EPOCH_TICK_S = 0.010

#: Trap code -> reason (the contract's guest_interface.traps). Out of fuel depends on the phase.
TRAP_REASONS: dict[Any, Reason] = {
    wasmtime.TrapCode.INTERRUPT: Reason.RULE_TIMEOUT,
    wasmtime.TrapCode.STACK_OVERFLOW: Reason.RULE_RESOURCE_LIMIT,
    wasmtime.TrapCode.ALLOCATION_TOO_LARGE: Reason.RULE_RESOURCE_LIMIT,
}
#: Out of fuel by the phase it happened in.
OUT_OF_FUEL_REASONS: dict[str, Reason] = {
    "prelude": Reason.RULE_ERROR,
    "load": Reason.RULE_RESOURCE_LIMIT,
    "call": Reason.RULE_RESOURCE_LIMIT,
}
#: Any trap the tables above do not name.
OTHER_TRAP = Reason.RULE_ERROR


@dataclass(frozen=True)
class Limits:
    call_fuel: int
    load_fuel: int
    prelude_fuel: int
    memory_bytes: int
    input_code_points: int
    output_bytes: int
    wall_ms: int


@dataclass
class Outcome:
    reduced: reply.Reduced
    fuel_used: dict[str, int]


def guest_request(
    *,
    mode: str,
    source: str,
    function: str | None,
    params: dict[str, Any] | None,
    text: str | None,
    diagnostics: bool,
) -> bytes:
    """The exact request bytes (the contract's guest_interface.request): fuel depends on them."""
    return canonical_json(
        {
            "mode": mode,
            "source": source,
            "function": function,
            "params": params,
            "text": text,
            "diagnostics": diagnostics,
        }
    )


class Host:
    """One per runner process: the engine, the snapshot module and the epoch ticker."""

    def __init__(
        self, engine: wasmtime.Engine, module: wasmtime.Module, contract: dict[str, Any]
    ) -> None:
        self.engine = engine
        self.module = module
        self.count_cap = int(contract["count_cap"])
        self.random_seed = str(contract["random"]["seed"]).encode("utf-8")
        self.linker = wasi.build_linker(engine, module)
        self.pre = self.linker.instantiate_pre(module)
        self._stop = threading.Event()
        self._ticker = threading.Thread(target=self._tick, name="epoch-ticker", daemon=True)
        self._ticker.start()

    @staticmethod
    def engine_for(contract: dict[str, Any]) -> wasmtime.Engine:
        config = wasmtime.Config()
        config.consume_fuel = True
        config.epoch_interruption = True
        config.max_wasm_stack = int(contract["limits_floor"]["wasm_stack_bytes"])
        return wasmtime.Engine(config)

    def _tick(self) -> None:
        while not self._stop.wait(EPOCH_TICK_S):
            self.engine.increment_epoch()

    def close(self) -> None:
        self._stop.set()

    def run_one(self, request: bytes, limits: Limits, *, inspect: bool = False) -> Outcome:
        """Run one guest call on a fresh instance; never raises for anything the guest does."""
        store = wasmtime.Store(self.engine)
        store.set_limits(memory_size=limits.memory_bytes)
        store.set_fuel(limits.prelude_fuel)
        store.set_epoch_deadline(max(1, math.ceil(limits.wall_ms / (EPOCH_TICK_S * 1000))))
        state = wasi.CallState(
            stdin=request,
            budgets={
                "prelude": limits.prelude_fuel,
                "load": limits.load_fuel,
                "call": limits.call_fuel,
            },
            output_limit=limits.output_bytes,
            random_seed=self.random_seed,
            final_phase="load" if inspect else "call",
        )
        wasi.bind(state)
        ended: Reason | None = None
        try:
            instance = self.pre.instantiate(store)
            run = instance.exports(store)["run"]
            assert isinstance(run, wasmtime.Func)
            run(store)
        except wasi.GuestEnd as end:
            ended = end.reason  # None: proc_exit, read what was replied
        except wasmtime.Trap as trap:
            if trap.trap_code == wasmtime.TrapCode.OUT_OF_FUEL:
                ended = OUT_OF_FUEL_REASONS[state.phase]
            else:
                ended = TRAP_REASONS.get(trap.trap_code, OTHER_TRAP)
        except wasmtime.WasmtimeError:
            ended = OTHER_TRAP
        finally:
            wasi.bind(None)
        try:
            left = store.get_fuel()
        except wasmtime.WasmtimeError:
            left = 0
        state.used[state.phase] = state.budgets[state.phase] - left
        fuel = {phase: state.used.get(phase, 0) for phase in ("prelude", "load", "call")}
        if state.fault is not None:
            return Outcome(reply.Reduced(reason=state.fault), fuel)
        if ended is not None:
            return Outcome(reply.Reduced(reason=ended), fuel)
        reduced = reply.reduce(
            bytes(state.reply),
            mode="inspect" if inspect else "run",
            output_limit=limits.output_bytes,
            count_cap=self.count_cap,
        )
        return Outcome(reduced, fuel)
