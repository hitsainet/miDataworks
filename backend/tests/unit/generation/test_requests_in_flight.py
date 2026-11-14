"""How many requests a generation run keeps in flight (017 TD-017.12; Review R2).

``requests_in_flight`` is the worker's reading of the run; ``_Ahead`` sends at most ``width``
units ahead of the one being decided (a stop wastes at most ``width - 1`` answers)."""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any

import pytest

from src.services.generation.run_engine import _Ahead, requests_in_flight


@pytest.mark.parametrize(
    ("mode", "path", "recorded", "width"),
    [
        ("carrier_instructed", "native", 4, 4),
        ("carrier_slot", "native", 3, 3),
        ("carrier_instructed", "relay", 4, 1),
        ("standard", "native", 4, 1),
        ("minimal_pairs", "native", 8, 1),
        ("carrier_slot", "native", None, 1),
    ],
)
def test_the_worker_reads_the_width_of_a_native_carrier_run_only(
    mode: str, path: str, recorded: int | None, width: int
) -> None:
    run = SimpleNamespace(mode=mode, engine_path=path, requests_in_flight=recorded)
    assert requests_in_flight(run) == width  # type: ignore[arg-type]


class _Engine:
    """Records which units were sent; each send waits until the test lets it finish."""

    def __init__(self) -> None:
        self.sent: list[int] = []
        self.lock = threading.Lock()

    def _send(self, loop: Any, template: Any, unit: Any, *rest: Any) -> dict[str, Any]:
        with self.lock:
            self.sent.append(unit.position)
        return {"generator": [(0, unit.position, None)]}

    def _response_format(self, template: Any) -> None:
        return None


def test_no_more_than_width_units_are_sent_ahead() -> None:
    engine = _Engine()
    units = [SimpleNamespace(position=i) for i in range(10)]
    template = SimpleNamespace(body={})
    ahead = _Ahead(engine, None, template, units, ["generator"], 1, None, None, 3)  # type: ignore[arg-type]
    try:
        assert ahead.take(0)["generator"][0][1] == 0
        ahead.close()  # waits for what was started: units 0-2 only
        assert sorted(engine.sent) == [0, 1, 2]
    finally:
        ahead.close()
    ahead = _Ahead(engine, None, template, units, ["generator"], 1, None, None, 3)  # type: ignore[arg-type]
    engine.sent.clear()
    try:
        for i in range(4):
            assert ahead.take(i)["generator"][0][1] == i, "answers come back in unit order"
    finally:
        ahead.close()
    assert sorted(engine.sent) == list(range(6)), "unit 3 taken: units 0-5 sent, no further"
