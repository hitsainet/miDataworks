"""``executor._jsonable``: a report with NumPy values is written as plain JSON, never failing the
step (live2, 2026-10-18)."""

from __future__ import annotations

import json

import numpy as np

from src.operators.executor import _jsonable


class Odd:
    def __str__(self) -> str:
        return "odd"


def test_numpy_values_become_plain_json_and_odd_values_their_text() -> None:
    report = {
        "k": np.int64(3),
        "share": np.float32(0.5),
        "sizes": np.array([1, 2]),
        "nested": [{"v": np.int32(7)}],
        "odd": Odd(),
    }
    out = _jsonable(report)
    assert out["k"] == 3 and type(out["k"]) is int
    assert out["share"] == 0.5 and out["sizes"] == [1, 2] and out["nested"] == [{"v": 7}]
    assert out["odd"] == "odd"
    json.dumps(out)  # plain JSON throughout
