"""Real rows of ``Arrrlex/models-under-pressure`` (read 2026-10-08), for the 2026-10-08 live findings.

The file keeps the shapes the code must meet, verbatim: every chat is a JSON STRING
(``'[{"role": "system", ...}, {"role": "user", ...}]'``), and the ``mental_health_balanced`` test
rows include the four whose user turn is ``"nan"`` — identical content, so one row key, labelled
twice ``low-stakes`` and twice ``high-stakes``. A fixture built to agree with the code (a real list,
distinct texts, one label per text) is how both defects stayed hidden.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

PATH = Path(__file__).parent / "detector_sets" / "models_under_pressure_rows_2026-10-08.json"
#: The user turn of the four copies that share one row key.
NAN_TURN = "nan"


@cache
def _doc() -> dict[str, Any]:
    return json.loads(PATH.read_text(encoding="utf-8"))


def mental_health_rows() -> list[dict[str, Any]]:
    """``{inputs, ids, labels, source_locator}`` in file order; ``inputs`` is JSON text."""
    return [dict(r) for r in _doc()["mental_health_balanced_test"]]


def nan_rows() -> list[dict[str, Any]]:
    return [r for r in mental_health_rows() if json.loads(r["inputs"])[-1]["content"] == NAN_TURN]


def shape(name: str) -> dict[str, Any]:
    """``toolace_balanced_test_with_tool_turn``, ``training_train_json_string`` or
    ``training_train_plain_text``."""
    return dict(_doc()[name])
